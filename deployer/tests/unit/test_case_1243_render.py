"""CASE-1243, render findings.

- 3-config R4: SpecContext.internal_base_url had no caller and pointed at the
  router, not Caddy; it is gone, and an unknown `from_spec` path is a
  validation error naming it (it was a KeyError traceback at render).
- 4-config R3: Garage's and Dex's configs carry secrets; on k8s they are
  Secrets, not ConfigMaps.
- 5-render R5: `network.http_port` was reserved and drift-checked but bound by
  no target; it is gone, and saved state moves to format 6.
- 5-render R6: a config file a render no longer produces is removed, not left
  behind with its secrets.
- 5-render R10: a `command` healthcheck runs under `timeout`, as the HTTP probe
  bounds itself.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _DEPLOYMENT_JSON_VERSION, _upgrade_state_payload
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_k8s
from wip_deploy.renderers.compose import conditional_config_paths
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend
from wip_deploy.spec.app import App
from wip_deploy.spec.deployment import NetworkSpec
from wip_deploy.spec.validators import _spec_context_paths, validate_all

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def disc() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(target: str, **kw):
    return build_deployment(BuildInputs(
        name="t", preset="standard", target=target, hostname="wip.example.org",
        compose_data_dir=Path("/tmp/wip-1243"), k8s_namespace="wip-t", **kw,
    ))


def _render(disc: Discovery, target: str, **kw):
    d = _deployment(target, **kw)
    store = Path(tempfile.mkdtemp())
    d.spec.secrets.location = str(store)
    s = ensure_secrets(d, disc.components, disc.apps, FileSecretBackend(store))
    return (render_k8s if target == "k8s" else render_compose)(d, disc.components, disc.apps, s)


# ── 3-config R4 ──────────────────────────────────────────────────────────


def test_internal_base_url_is_gone() -> None:
    assert "network.internal_base_url" not in _spec_context_paths()
    assert "network.external_base_url" in _spec_context_paths()


def test_an_unknown_from_spec_path_is_a_validation_error(disc: Discovery) -> None:
    raw = yaml.safe_load((REPO_ROOT / "apps/wip-cooking/wip-app.yaml").read_text())
    raw["spec"]["env"]["required"].append(
        {"name": "INTERNAL", "source": {"from_spec": "network.internal_base_url"}}
    )
    apps = [a for a in disc.apps if a.metadata.name != "wip-cooking"] + [App.model_validate(raw)]
    d = _deployment("compose", apps=["wip-cooking"])
    errors = [str(e) for e in validate_all(d, disc.components, apps).errors]
    assert any("reads from_spec 'network.internal_base_url'" in e for e in errors), errors


# ── 4-config R3 ──────────────────────────────────────────────────────────


def test_dex_and_garage_config_are_k8s_secrets(disc: Discovery) -> None:
    tree = _render(disc, "k8s", auth_mode="oidc")
    docs = [d for e in tree.files.values() for d in yaml.safe_load_all(e.content) if isinstance(d, dict)]
    by_name = {(d["kind"], d["metadata"]["name"]) for d in docs if "metadata" in d}
    for name in ("wip-dex-config", "wip-garage-config"):
        assert ("Secret", name) in by_name
        assert ("ConfigMap", name) not in by_name
    mounts = {
        v["name"]: v for d in docs if d.get("kind") in ("Deployment", "StatefulSet")
        if d["metadata"]["name"] in ("wip-dex", "wip-garage")
        for v in d["spec"]["template"]["spec"]["volumes"]
    }
    assert mounts["config"].get("secret", {}).get("secretName") in ("wip-dex-config", "wip-garage-config")


# ── 5-render R5 ──────────────────────────────────────────────────────────


def test_http_port_is_gone_and_a_saved_one_is_dropped(disc: Discovery) -> None:
    assert "http_port" not in NetworkSpec.model_fields
    payload = {"wip_deploy_format_version": 5, "deployment": _deployment("compose").model_dump(mode="json")}
    payload["deployment"]["spec"]["network"]["http_port"] = 8080
    out = _upgrade_state_payload(payload)
    assert out["wip_deploy_format_version"] == _DEPLOYMENT_JSON_VERSION == 6
    assert "http_port" not in out["deployment"]["spec"]["network"]


# ── 5-render R6 ──────────────────────────────────────────────────────────


def test_a_config_file_the_render_no_longer_writes_is_removed(disc: Discovery, tmp_path: Path) -> None:
    with_garage = _render(disc, "compose")
    with_garage.write(tmp_path)
    garage = tmp_path / "config/garage/garage.toml"
    assert garage.exists()

    without = _render(disc, "compose", remove=["garage"])
    assert Path("config/garage/garage.toml") in without.obsolete
    without.write(tmp_path)
    assert not garage.exists()
    # What the render still writes is never removed.
    assert (tmp_path / "config/caddy/Caddyfile").exists()


def test_every_conditional_path_is_one_a_render_can_write(disc: Discovery) -> None:
    written = set(_render(disc, "compose", auth_mode="oidc").files)
    assert {Path(p) for p in conditional_config_paths()} <= written


# ── 5-render R10 ─────────────────────────────────────────────────────────


def test_a_command_healthcheck_runs_under_timeout(disc: Discovery) -> None:
    doc = yaml.safe_load(_render(disc, "compose").files[Path("docker-compose.yaml")].content)
    for svc in ("mongodb", "nats", "postgres", "router"):
        hc = doc["services"][svc]["healthcheck"]
        timeout = hc["timeout"].removesuffix("s")
        assert hc["test"][1].startswith(f"timeout {timeout} "), (svc, hc["test"])
