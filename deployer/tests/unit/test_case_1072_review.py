"""Review fixes for the scoped apply, the edge-reach warning and the cluster
ranges (CASE-1072).

- A scoped verb on a k8s install last applied before wip-caddy became the
  front door applies the whole install once (with prune), so the old
  per-route Ingresses go.
- A scoped apply names services new to the render, on both targets.
- `remove-module` removes the container only after the apply succeeded.
- The edge-reach warning names each missing range kind.
- An IPv4-mapped cluster range is refused; ranges are refused off k8s;
  `redeploy --cluster-cidr` replaces the saved ranges.
- The attach policy carries `app_edge` only when false.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from wip_deploy.apply import changed_outside_scope
from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import (
    _apply_and_persist_mutation,
    _k8s_render_predates_front_door,
    _warn_app_edge_reach,
    app,
)
from wip_deploy.config_gen.config_hash import CONFIG_HASH_KEY
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers.base import FileTree
from wip_deploy.spec.deployment import K8sClusterRanges

from ._kube import unit_cluster
from .renderers.test_compose import REPO_ROOT, _minimal_compose
from .renderers.test_k8s import _k8s_deployment

runner = CliRunner()


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _workload(name: str, h: str) -> str:
    return yaml.safe_dump({
        "kind": "Deployment", "metadata": {"name": f"wip-{name}"},
        "spec": {"template": {"metadata": {"annotations": {CONFIG_HASH_KEY: h}}}},
    })


# ── Finding 1: the one full apply after the front-door change ─────────


def test_a_render_without_wip_caddy_predates_the_front_door(tmp_path: Path) -> None:
    d = _k8s_deployment()
    assert not _k8s_render_predates_front_door(d, tmp_path)  # nothing rendered yet
    (tmp_path / "namespace.yaml").write_text("kind: Namespace\n")
    (tmp_path / "services").mkdir()
    assert _k8s_render_predates_front_door(d, tmp_path)
    (tmp_path / "services" / "caddy.yaml").write_text(_workload("caddy", "c"))
    assert not _k8s_render_predates_front_door(d, tmp_path)
    assert not _k8s_render_predates_front_door(_minimal_compose(), tmp_path)


def _mutate(tmp_path: Path, discovery: Discovery, scope: list[str]) -> MagicMock:
    d = _k8s_deployment(apps=["wip-kb"])
    d.spec.platform.k8s.kube = unit_cluster()
    with (
        patch("wip_deploy.cli._validate_or_exit"),
        patch("wip_deploy.cli._ensure_secrets_via_spec", return_value=MagicMock()),
        patch("wip_deploy.cli._refuse_runtime_reservation_collisions"),
        patch("wip_deploy.cli._refuse_attachment_collisions"),
        patch("wip_deploy.cli._render_tree", return_value=FileTree()),
        patch("wip_deploy.cli._persist_deployment"),
        patch("wip_deploy.cli.apply_k8s") as apply,
    ):
        apply.return_value = MagicMock(healthy=True, post_install_skipped=[])
        _apply_and_persist_mutation(
            d, discovery.components, discovery.apps, tmp_path, "label",
            services_scope=scope, repo_root=REPO_ROOT,
        )
    return apply


def test_a_scoped_verb_on_a_pre_front_door_install_applies_everything(
    tmp_path: Path, discovery: Discovery, capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "namespace.yaml").write_text("kind: Namespace\n")
    apply = _mutate(tmp_path, discovery, ["wip-kb"])
    assert "services_scope" not in apply.call_args.kwargs  # full apply, with prune
    assert "applying the whole install once" in capsys.readouterr().out


def test_a_scoped_verb_after_the_front_door_stays_scoped(tmp_path: Path, discovery: Discovery) -> None:
    (tmp_path / "namespace.yaml").write_text("kind: Namespace\n")
    (tmp_path / "services").mkdir()
    (tmp_path / "services" / "caddy.yaml").write_text(_workload("caddy", "c"))
    apply = _mutate(tmp_path, discovery, ["wip-kb"])
    assert apply.call_args.kwargs["services_scope"] == ["wip-kb"]


# ── Finding 3: services new to the render ─────────────────────────────


def test_a_service_new_to_the_render_joins_the_scope_on_compose(tmp_path: Path) -> None:
    def compose(names: list[str]) -> str:
        return yaml.safe_dump({"services": {
            n: {"image": f"{n}:1", "labels": {CONFIG_HASH_KEY: "h"}} for n in names
        }})

    (tmp_path / "docker-compose.yaml").write_text(compose(["registry", "wip-kb"]))
    tree = FileTree()
    tree.add("docker-compose.yaml", compose(["registry", "wip-kb", "app-edge"]))
    assert changed_outside_scope(tree, tmp_path, "compose", ["wip-kb"]) == ["app-edge"]


def test_nothing_is_named_without_a_previous_render(tmp_path: Path) -> None:
    tree = FileTree()
    tree.add("services/router.yaml", _workload("router", "r"))
    assert changed_outside_scope(tree, tmp_path, "k8s", ["wip-kb"]) == []


# ── Finding 4: remove-module stops the container last ─────────────────


def _state(install_dir: Path, modules: list[str]) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "deployment.deployer-state").write_text(json.dumps({
        "wip_deploy_format_version": 2,
        "deployment": {
            "metadata": {"name": "t"},
            "spec": {
                "target": "compose", "apps": [], "modules": {"optional": modules},
                "auth": {"mode": "hybrid", "gateway": True},
                "network": {"hostname": "localhost"}, "images": {},
                "platform": {"compose": {"data_dir": "/tmp/d"}},
                "secrets": {"backend": "file", "location": "/tmp/s"}, "apply": {},
            },
        },
    }))


def test_a_failed_apply_leaves_the_module_running(tmp_path: Path) -> None:
    import typer

    _state(tmp_path, ["mcp-server"])
    with (
        patch("wip_deploy.apply.stop_and_remove_container") as stop,
        patch("wip_deploy.cli._apply_and_persist_mutation", side_effect=typer.Exit(2)),
    ):
        result = runner.invoke(app, ["remove-module", "mcp-server", "--install-dir", str(tmp_path)])
    assert result.exit_code == 2
    stop.assert_not_called()


def test_a_successful_apply_then_removes_the_container(tmp_path: Path) -> None:
    order: list[str] = []
    _state(tmp_path, ["mcp-server"])
    with (
        patch("wip_deploy.apply.stop_and_remove_container", side_effect=lambda *_: order.append("stop")),
        patch("wip_deploy.cli._apply_and_persist_mutation", side_effect=lambda *a, **k: order.append("apply")),
    ):
        result = runner.invoke(app, ["remove-module", "mcp-server", "--install-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert order == ["apply", "stop"]


# ── Finding 2: the warning names each missing kind ────────────────────


def _edge_k8s(**ranges: list[str]):
    d = _k8s_deployment(modules=["mcp-server", "app-edge"])
    d.spec.platform.k8s.cluster_ranges = K8sClusterRanges(**ranges)
    return d


def test_pods_and_services_without_nodes_still_warn(capsys: pytest.CaptureFixture[str]) -> None:
    _warn_app_edge_reach(_edge_k8s(pods=["10.42.0.0/16"], services=["10.43.0.0/16"]))
    err = capsys.readouterr().err
    assert "Missing: nodes" in err and "control plane" in err
    assert "pods (" not in err and "services (" not in err


def test_nodes_alone_warns_about_pods_and_services(capsys: pytest.CaptureFixture[str]) -> None:
    _warn_app_edge_reach(_edge_k8s(nodes=["192.168.1.20/32"]))
    err = capsys.readouterr().err
    assert "nodes (" not in err and "pods (" in err and "services (" in err


# ── Findings 6 and 7: the cluster ranges ──────────────────────────────


@pytest.mark.parametrize("cidr", ["::ffff:192.168.1.20/128", "::ffff:192.168.0.0/112"])
def test_an_ipv4_mapped_range_is_refused(cidr: str) -> None:
    with pytest.raises(ValueError, match="IPv4-mapped"):
        K8sClusterRanges(nodes=[cidr])


def test_plain_ranges_of_both_families_pass() -> None:
    r = K8sClusterRanges(nodes=["192.168.1.20/32"], pods=["fd42::/56"])
    assert r.nodes == ["192.168.1.20/32"] and r.pods == ["fd42::/56"]


def test_cluster_ranges_are_refused_off_k8s() -> None:
    with pytest.raises(ValueError, match="k8s target only"):
        build_deployment(BuildInputs(
            target="compose", variant="dev", hostname="localhost", tls="internal",
            compose_data_dir=Path("/tmp/d"), k8s_cluster_cidrs=["nodes=192.168.1.20/32"],
        ))


def _k8s_state(install_dir: Path, ranges: dict[str, list[str]] | None = None) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    d = _k8s_deployment(modules=["mcp-server", "app-edge"])
    if ranges:
        d.spec.platform.k8s.cluster_ranges = K8sClusterRanges(**ranges)
    (install_dir / "deployment.deployer-state").write_text(json.dumps({
        "wip_deploy_format_version": 2, "deployment": d.model_dump(mode="json"),
    }))


def test_redeploy_replaces_the_saved_ranges(tmp_path: Path) -> None:
    _k8s_state(tmp_path, {"pods": ["10.42.0.0/16"]})
    with patch("wip_deploy.cli._apply_and_persist_mutation") as apply:
        result = runner.invoke(app, [
            "redeploy", "--install-dir", str(tmp_path),
            "--cluster-cidr", "nodes=192.168.1.20/32", "--cluster-cidr", "services=10.43.0.0/16",
        ])
    assert result.exit_code == 0, result.output
    ranges = apply.call_args.args[0].spec.platform.k8s.cluster_ranges
    assert (ranges.nodes, ranges.services, ranges.pods) == (["192.168.1.20/32"], ["10.43.0.0/16"], [])
    assert "new cluster ranges" in apply.call_args.args[4]


def test_redeploy_refuses_a_bad_range_and_a_compose_install(tmp_path: Path) -> None:
    _k8s_state(tmp_path / "k8s")
    bad = runner.invoke(app, ["redeploy", "--install-dir", str(tmp_path / "k8s"),
                              "--cluster-cidr", "nodes=::ffff:10.0.0.1/128"])
    assert bad.exit_code == 2 and "IPv4-mapped" in bad.output

    _state(tmp_path / "compose", [])
    off = runner.invoke(app, ["redeploy", "--install-dir", str(tmp_path / "compose"),
                              "--cluster-cidr", "nodes=192.168.1.20/32"])
    assert off.exit_code == 2 and "k8s installs only" in off.output
