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
