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
