"""Verbs that run `compose up` check what it left and honour their health wait.

podman-compose exits 0 when podman refuses to create a container, so a
successful-looking up can leave a rendered service without one. `install`
checked for that; `up` and `rebuild` did not, and `up` dropped its own
health-wait timeout and printed success anyway. Every health timeout now
names the containers that were not healthy.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from wip_deploy.apply import (
    ApplyError,
    _await_healthy,
    _compose_project,
    rebuild_compose_services,
    up_compose_install,
)
from wip_deploy.cli import app

runner = CliRunner()

_PODMAN = "/usr/bin/podman"


def _write_compose(install_dir: Path, *, name: str | None, services: dict[str, dict]) -> Path:
    install_dir.mkdir(parents=True, exist_ok=True)
    doc: dict = {"services": services}
    if name is not None:
        doc["name"] = name
    path = install_dir / "docker-compose.yaml"
    path.write_text(yaml.safe_dump(doc))
    return path


def _two_services(install: str) -> dict[str, dict]:
    return {
        "registry": {"image": "x", "container_name": f"{install}_wip-registry"},
        "def-store": {"image": "y", "container_name": f"{install}_wip-def-store"},
    }


def _runtime(present: list[str]):
    """subprocess.run stand-in: `podman ps` lists ``present``; anything else
    (the compose up) succeeds. Records the project label the listing asked for."""
    asked: list[str] = []

    def run(cmd, *args, **kwargs):
        if cmd[:2] == ["podman", "ps"]:
            label = next(a for a in cmd if a.startswith("label="))
            asked.append(label.split("=", 2)[2])
            return MagicMock(returncode=0, stdout="\n".join(present) + "\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    return run, asked


class TestComposeProject:
    def test_pinned_name_wins_over_the_directory(self, tmp_path: Path) -> None:
        f = _write_compose(tmp_path / "elsewhere", name="tc01", services={})
        assert _compose_project(f) == "tc01"

    def test_unpinned_file_uses_the_directory_as_compose_does(self, tmp_path: Path) -> None:
        f = _write_compose(tmp_path / "older", name=None, services={})
        assert _compose_project(f) == "older"


class TestUpChecksContainers:
    def test_missing_container_fails_up(self, tmp_path: Path) -> None:
        # The install directory's basename is not the install name: the check
        # must ask by the pinned project, or it would see nothing at all.
        d = tmp_path / "some-dir"
        _write_compose(d, name="tc01", services=_two_services("tc01"))
        run, asked = _runtime(["tc01_wip-registry"])
        with (
            patch("wip_deploy.apply.shutil.which", return_value=_PODMAN),
            patch("wip_deploy.apply.subprocess.run", side_effect=run),
            pytest.raises(ApplyError, match="tc01_wip-def-store"),
        ):
            up_compose_install(install_dir=d, wait=False)
        assert asked == ["tc01"]

    def test_every_container_present_passes(self, tmp_path: Path) -> None:
        _write_compose(tmp_path, name="tc01", services=_two_services("tc01"))
        run, _ = _runtime(["tc01_wip-registry", "tc01_wip-def-store"])
        with (
            patch("wip_deploy.apply.shutil.which", return_value=_PODMAN),
            patch("wip_deploy.apply.subprocess.run", side_effect=run),
        ):
            up_compose_install(install_dir=tmp_path, wait=False)


class TestRebuildChecksContainers:
    def test_missing_rebuilt_container_fails_fast(self, tmp_path: Path) -> None:
        # Before, rebuild polled the missing container until the timeout and
        # then called it unhealthy; now it is named as never created.
        _write_compose(tmp_path, name="tc01", services=_two_services("tc01"))
        run, _ = _runtime(["tc01_wip-def-store"])
        with (
            patch("wip_deploy.apply.shutil.which", return_value=_PODMAN),
            patch("wip_deploy.apply.subprocess.run", side_effect=run),
            pytest.raises(ApplyError, match="without a container: tc01_wip-registry"),
        ):
            rebuild_compose_services(install_dir=tmp_path, services=["registry"], wait=True)

    def test_only_the_rebuilt_services_are_checked(self, tmp_path: Path) -> None:
        _write_compose(tmp_path, name="tc01", services=_two_services("tc01"))
        run, _ = _runtime(["tc01_wip-registry"])
        with (
            patch("wip_deploy.apply.shutil.which", return_value=_PODMAN),
            patch("wip_deploy.apply.subprocess.run", side_effect=run),
        ):
            rebuild_compose_services(install_dir=tmp_path, services=["registry"], wait=False)


class TestUpHonoursTimeout:
    def _compose(self, tmp_path: Path) -> None:
        services = _two_services("tc01")
        for block in services.values():
            block["healthcheck"] = {"test": ["CMD", "true"]}
        _write_compose(tmp_path, name="tc01", services=services)

    def test_timeout_raises_naming_what_was_not_healthy(self, tmp_path: Path) -> None:
        self._compose(tmp_path)
        statuses = {"tc01_wip-registry": "healthy", "tc01_wip-def-store": "starting"}
        with (
            patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"]),
            patch("wip_deploy.apply._run_up"),
            patch("wip_deploy.apply._check_containers_created"),
            patch("wip_deploy.apply._compose_ps", return_value=statuses),
            pytest.raises(ApplyError, match=r"timed out after 0 s; not healthy: tc01_wip-def-store\."),
        ):
            up_compose_install(install_dir=tmp_path, wait=True, timeout_seconds=0)

    def test_all_healthy_returns(self, tmp_path: Path) -> None:
        self._compose(tmp_path)
        statuses = {"tc01_wip-registry": "healthy", "tc01_wip-def-store": "healthy"}
        with (
            patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"]),
            patch("wip_deploy.apply._run_up"),
            patch("wip_deploy.apply._check_containers_created"),
            patch("wip_deploy.apply._compose_ps", return_value=statuses),
        ):
            up_compose_install(install_dir=tmp_path, wait=True, timeout_seconds=0)

    def test_cli_up_failure_is_not_reported_as_brought_up(self, tmp_path: Path) -> None:
        self._compose(tmp_path)
        with (
            patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"]),
            patch("wip_deploy.apply._run_up"),
            patch("wip_deploy.apply._check_containers_created"),
            patch("wip_deploy.apply._compose_ps", return_value={}),
        ):
            r = runner.invoke(
                app, ["up", "--install-dir", str(tmp_path), "--wait-timeout", "0"]
            )
        assert r.exit_code == 1
        assert "Brought up" not in r.output
        assert "tc01_wip-def-store, tc01_wip-registry" in r.output


class TestAwaitHealthy:
    def test_unlisted_container_counts_as_not_healthy(self, tmp_path: Path) -> None:
        with patch("wip_deploy.apply._compose_ps", return_value={"a": "healthy"}):
            assert _await_healthy(tmp_path, ["podman-compose"], {"a", "b"}, 0) == ["b"]

    def test_nothing_expected_is_nothing_pending(self, tmp_path: Path) -> None:
        with patch("wip_deploy.apply._compose_ps") as ps:
            assert _await_healthy(tmp_path, ["podman-compose"], set(), 0) == []
        ps.assert_not_called()


class TestNoWaitIsNotGreen:
    def test_up_no_wait_says_health_not_checked(self, tmp_path: Path) -> None:
        _write_compose(tmp_path, name="tc01", services=_two_services("tc01"))
        with (
            patch("wip_deploy.apply._detect_compose_cmd", return_value=["podman-compose"]),
            patch("wip_deploy.apply._run_up"),
            patch("wip_deploy.apply._check_containers_created"),
        ):
            r = runner.invoke(app, ["up", "--install-dir", str(tmp_path), "--no-wait"])
        assert r.exit_code == 0, r.output
        assert "health not checked" in r.output
        assert "✓" not in r.output


# ────────────────────────────────────────────────────────────────────
# Item 2: --no-wait belongs to its own run; on_timeout is gone
# ────────────────────────────────────────────────────────────────────

import json  # noqa: E402

from wip_deploy.cli import (  # noqa: E402
    _DEPLOYMENT_JSON_VERSION,
    _for_this_run,
    _load_deployment,
    _upgrade_state_payload,
)
from wip_deploy.spec.deployment import (  # noqa: E402
    ApplySpec,
    AuthSpec,
    ComposePlatform,
    Deployment,
    DeploymentMetadata,
    DeploymentSpec,
    ImagesSpec,
    NetworkSpec,
    PlatformSpec,
    SecretsSpec,
)


def _deployment() -> Deployment:
    return Deployment(
        metadata=DeploymentMetadata(name="t"),
        spec=DeploymentSpec(
            target="compose",
            auth=AuthSpec(mode="api-key-only", gateway=False),
            network=NetworkSpec(hostname="wip.local", tls="internal"),
            images=ImagesSpec(),
            platform=PlatformSpec(compose=ComposePlatform(data_dir=Path("/tmp/d"))),
            secrets=SecretsSpec(backend="file", location="/tmp/s"),
            apply=ApplySpec(timeout_seconds=600),
        ),
    )


def _v3_state(apply_block: dict) -> dict:
    payload = {"wip_deploy_format_version": 3, "deployment": _deployment().model_dump(mode="json")}
    payload["deployment"]["spec"]["apply"] = apply_block
    return payload


class TestNoWaitIsThisRunOnly:
    def test_the_saved_deployment_still_waits(self) -> None:
        saved = _deployment()
        applied = _for_this_run(saved, no_wait=True)
        assert applied.spec.apply.wait is False
        assert saved.spec.apply.wait is True
        # The timeout is the install's own pace and travels with both.
        assert applied.spec.apply.timeout_seconds == saved.spec.apply.timeout_seconds == 600

    def test_without_the_flag_the_same_deployment_is_applied(self) -> None:
        d = _deployment()
        assert _for_this_run(d, no_wait=False) is d


class TestStateV3ToV4:
    def test_saved_fail_is_dropped_and_a_sticky_no_wait_is_cleared(self, tmp_path: Path) -> None:
        state = _v3_state({"wait": False, "timeout_seconds": 600, "on_timeout": "fail"})
        out = _upgrade_state_payload(state)
        assert out["wip_deploy_format_version"] == _DEPLOYMENT_JSON_VERSION == 4
        assert out["deployment"]["spec"]["apply"] == {"wait": True, "timeout_seconds": 600}

        (tmp_path / "deployment.deployer-state").write_text(json.dumps(state))
        loaded = _load_deployment(tmp_path)
        assert loaded.spec.apply.wait is True
        assert loaded.spec.apply.timeout_seconds == 600

    @pytest.mark.parametrize("value", ["warn", "continue"])
    def test_a_hand_set_warn_or_continue_is_refused_by_name(
        self, tmp_path: Path, value: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        state = _v3_state({"wait": True, "timeout_seconds": 300, "on_timeout": value})
        (tmp_path / "deployment.deployer-state").write_text(json.dumps(state))
        import typer

        with pytest.raises(typer.Exit):
            _load_deployment(tmp_path)
        assert "on_timeout" in capsys.readouterr().err

    def test_the_model_has_no_on_timeout(self) -> None:
        assert "on_timeout" not in ApplySpec.model_fields


# ────────────────────────────────────────────────────────────────────
# Item 4: removing an app on k8s prunes it, however the scope widened
# ────────────────────────────────────────────────────────────────────


class TestRemovalPrunesOnK8s:
    """Dropping an app rewrites the front door's Caddyfile, whose digest is on
    wip-caddy's pod template, so widening names caddy: the empty scope became
    ["caddy"], a scoped apply that never prunes, and the app kept running."""

    def _run(self, target: str, *, removes: bool, widened: list[str]) -> dict:
        from wip_deploy.cli import _apply_and_persist_mutation

        deployment = _deployment()
        if target == "k8s":
            from wip_deploy.spec.deployment import K8sPlatform

            from ._kube import unit_cluster

            spec = deployment.spec.model_dump()
            spec.update(
                target="k8s",
                platform=PlatformSpec(k8s=K8sPlatform(namespace="wip", kube=unit_cluster())).model_dump(),
            )
            deployment = Deployment(metadata=deployment.metadata, spec=DeploymentSpec(**spec))
        result = MagicMock(healthy=True, post_install_skipped=[], health_checked=True)
        with (
            patch("wip_deploy.cli._validate_or_exit"),
            patch("wip_deploy.cli._select_cluster_or_exit"),
            patch("wip_deploy.cli._ensure_secrets_via_spec"),
            patch("wip_deploy.cli._refuse_runtime_reservation_collisions"),
            patch("wip_deploy.cli._refuse_attachment_collisions"),
            patch("wip_deploy.cli._render_tree"),
            patch("wip_deploy.cli._k8s_render_predates_front_door", return_value=False),
            patch("wip_deploy.cli.changed_outside_scope", return_value=widened),
            patch("wip_deploy.cli._persist_deployment"),
            patch("wip_deploy.cli.apply_k8s", return_value=result) as k8s,
            patch("wip_deploy.cli.apply_compose", return_value=result) as compose,
        ):
            _apply_and_persist_mutation(
                deployment, [], [], Path("/tmp/x"), "test",
                services_scope=[], removes_workloads=removes,
            )
        return (k8s if target == "k8s" else compose).call_args.kwargs

    def test_k8s_removal_applies_the_whole_tree_even_when_widened(self) -> None:
        kwargs = self._run("k8s", removes=True, widened=["caddy"])
        assert "services_scope" not in kwargs  # full tree, with prune

    def test_a_config_only_k8s_verb_still_narrows_to_what_changed(self) -> None:
        # group-map, app-namespace: nothing to delete, so the widened scope
        # (the service that reads the changed config) is the right apply.
        kwargs = self._run("k8s", removes=False, widened=["registry"])
        assert kwargs["services_scope"] == ["registry"]

    def test_compose_removal_keeps_its_config_only_scope(self) -> None:
        kwargs = self._run("compose", removes=True, widened=[])
        assert kwargs["services_scope"] == []


# ────────────────────────────────────────────────────────────────────
# Item 5: failures name their cause; hooks have deadlines
# ────────────────────────────────────────────────────────────────────

import subprocess  # noqa: E402

from wip_deploy.apply import _run_hook, _wait_k8s_rollout  # noqa: E402


class TestRolloutFailureNamesItsCause:
    def _wait(self, run_side_effect, timeout: int = 60) -> list[str]:
        d = _deployment()
        d.spec.apply.timeout_seconds = timeout
        with (
            patch("wip_deploy.apply._expected_workloads",
                  return_value=[("Deployment", "wip-registry"), ("StatefulSet", "wip-mongodb")]),
            patch("wip_deploy.apply.kubectl", return_value=["kubectl"]),
            patch("wip_deploy.apply.subprocess.run", side_effect=run_side_effect),
        ):
            return _wait_k8s_rollout(install_dir=Path("/tmp/x"), ns="wip", components=[], apps=[], deployment=d)

    def test_a_refused_kubectl_is_reported_in_its_own_words(self) -> None:
        def run(cmd, *a, **k):
            raise subprocess.CalledProcessError(
                1, cmd, stderr='Error from server (Forbidden): deployments.apps "wip-registry" is forbidden'
            )
        out = self._wait(run)
        assert out == ['deployment/wip-registry: Error from server (Forbidden): '
                       'deployments.apps "wip-registry" is forbidden']

    def test_all_rolled_out_is_empty(self) -> None:
        assert self._wait(lambda *a, **k: MagicMock(returncode=0)) == []

    def test_workloads_the_budget_never_reached_are_named(self) -> None:
        # The clock reads 0 when the deadline is set and 100 s on afterwards.
        clock = iter([0.0] + [100.0] * 10)
        with patch("wip_deploy.apply.time.time", side_effect=lambda: next(clock)):
            out = self._wait(lambda *a, **k: MagicMock(returncode=0), timeout=1)
        assert out == [
            "deployment/wip-registry: not checked, the wait's budget was spent",
            "statefulset/wip-mongodb: not checked, the wait's budget was spent",
        ]


class TestHooksHaveDeadlines:
    def test_a_hook_that_never_returns_fails_the_apply_by_name(self) -> None:
        with (
            patch("wip_deploy.apply.subprocess.run",
                  side_effect=subprocess.TimeoutExpired(["sh"], 300)) as run,
            pytest.raises(ApplyError, match=r"'init' on 'registry' did not finish within 300 s"),
        ):
            _run_hook(["sh"], cwd=None, label="'init' on 'registry'", timeout_seconds=300)
        assert run.call_args.kwargs["timeout"] == 300

    def test_a_failing_hook_still_reports_its_exit(self) -> None:
        with (
            patch("wip_deploy.apply.subprocess.run",
                  side_effect=subprocess.CalledProcessError(3, ["sh"])),
            pytest.raises(ApplyError, match=r"failed \(exit 3\)"),
        ):
            _run_hook(["sh"], cwd=None, label="'init' on 'registry'", timeout_seconds=300)
