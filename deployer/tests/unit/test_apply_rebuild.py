"""Tests for the per-service rebuild path.

Exercises ``apply.rebuild_compose_services`` with a synthetic install
directory and mocked subprocess. We don't spin up real containers —
the contract under test is the command construction and the
validation layer (missing dirs, unknown services, healthcheck
detection).

The CLI verb itself is wrapped in one happy-path test that proves
the wiring (typer arg parsing → apply call) without re-testing the
internals.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from wip_deploy.apply import ApplyError, rebuild_compose_services
from wip_deploy.cli import app

runner = CliRunner()


def _write_compose(install_dir: Path, services: dict[str, dict]) -> None:
    """Write a minimal docker-compose.yaml with the given services."""
    import yaml

    install_dir.mkdir(parents=True, exist_ok=True)
    compose = {"services": services}
    (install_dir / "docker-compose.yaml").write_text(yaml.safe_dump(compose))


# ────────────────────────────────────────────────────────────────────
# rebuild_compose_services — error paths
# ────────────────────────────────────────────────────────────────────


class TestRebuildErrors:
    def test_missing_install_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ApplyError, match="no docker-compose.yaml"):
            rebuild_compose_services(
                install_dir=tmp_path / "does-not-exist",
                services=["mcp-server"],
            )

    def test_missing_compose_file_raises(self, tmp_path: Path) -> None:
        # install_dir exists but no compose file
        with pytest.raises(ApplyError, match="no docker-compose.yaml"):
            rebuild_compose_services(
                install_dir=tmp_path,
                services=["mcp-server"],
            )

    def test_unknown_service_raises_with_available_list(self, tmp_path: Path) -> None:
        _write_compose(
            tmp_path,
            {"mcp-server": {"image": "x"}, "registry": {"image": "y"}},
        )
        with pytest.raises(ApplyError) as exc:
            rebuild_compose_services(
                install_dir=tmp_path,
                services=["nonexistent"],
            )
        msg = str(exc.value)
        assert "unknown service(s): nonexistent" in msg
        # Should list the actual available services so the user sees the typo.
        assert "mcp-server" in msg
        assert "registry" in msg

    def test_partial_unknown_services_listed(self, tmp_path: Path) -> None:
        _write_compose(tmp_path, {"mcp-server": {"image": "x"}})
        with pytest.raises(ApplyError, match="unknown service.*bogus.*missing"):
            rebuild_compose_services(
                install_dir=tmp_path,
                services=["mcp-server", "bogus", "missing"],
            )


# ────────────────────────────────────────────────────────────────────
# rebuild_compose_services — happy path command construction
# ────────────────────────────────────────────────────────────────────


class TestRebuildCommand:
    def test_passes_service_names_with_force_recreate_and_build(
        self, tmp_path: Path
    ) -> None:
        _write_compose(
            tmp_path,
            {"mcp-server": {"image": "x"}, "registry": {"image": "y"}},
        )

        with (
            patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/podman-compose"),
            patch("wip_deploy.apply.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            rebuild_compose_services(
                install_dir=tmp_path,
                services=["mcp-server", "registry"],
                wait=False,
            )

        # First call is the `up` invocation.
        cmd = mock_run.call_args_list[0].args[0]
        # Tail order: ... up -d --build --force-recreate <svc> <svc>
        assert "up" in cmd and "-d" in cmd
        assert "--build" in cmd
        assert "--force-recreate" in cmd
        # Both service names appended after the flags.
        assert cmd.index("mcp-server") > cmd.index("--force-recreate")
        assert "registry" in cmd

    def test_compose_failure_surfaces_as_apply_error(
        self, tmp_path: Path
    ) -> None:
        _write_compose(tmp_path, {"mcp-server": {"image": "x"}})

        import subprocess as _sub

        with (
            patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/podman-compose"),
            patch(
                "wip_deploy.apply.subprocess.run",
                side_effect=_sub.CalledProcessError(returncode=2, cmd=["podman-compose"]),
            ),pytest.raises(ApplyError, match="podman-compose up failed")
        ):
            rebuild_compose_services(
                install_dir=tmp_path,
                services=["mcp-server"],
                wait=False,
            )


# ────────────────────────────────────────────────────────────────────
# rebuild_compose_services — wait / no-wait
# ────────────────────────────────────────────────────────────────────


class TestRebuildWait:
    def test_no_wait_skips_polling(self, tmp_path: Path) -> None:
        _write_compose(
            tmp_path,
            {
                "mcp-server": {
                    "image": "x",
                    "healthcheck": {"test": ["CMD", "true"]},
                }
            },
        )

        with (
            patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/podman-compose"),
            patch("wip_deploy.apply.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            rebuild_compose_services(
                install_dir=tmp_path,
                services=["mcp-server"],
                wait=False,
            )

        # Only the `up` call — no `ps` poll.
        ps_calls = [
            c for c in mock_run.call_args_list
            if "ps" in c.args[0]
        ]
        assert ps_calls == []

    def test_service_without_healthcheck_skips_wait(self, tmp_path: Path) -> None:
        # When the requested service has no healthcheck, wait=True is a no-op
        # (no point polling for a state that'll never report).
        _write_compose(tmp_path, {"caddy": {"image": "x"}})

        with (
            patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/podman-compose"),
            patch("wip_deploy.apply.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            rebuild_compose_services(
                install_dir=tmp_path,
                services=["caddy"],
                wait=True,
            )

        ps_calls = [c for c in mock_run.call_args_list if "ps" in c.args[0]]
        assert ps_calls == []


# ────────────────────────────────────────────────────────────────────
# CLI verb wiring
# ────────────────────────────────────────────────────────────────────


class TestRebuildCli:
    def test_rebuild_appears_in_help(self) -> None:
        r = runner.invoke(app, ["--help"])
        assert r.exit_code == 0
        assert "rebuild" in r.output

    def test_rebuild_help_lists_args_and_options(self) -> None:
        r = runner.invoke(app, ["rebuild", "--help"])
        assert r.exit_code == 0
        assert "SERVICES" in r.output
        assert "--install-dir" in r.output
        assert "--name" in r.output
        assert "--no-wait" in r.output

    def test_rebuild_no_args_errors(self) -> None:
        r = runner.invoke(app, ["rebuild"])
        # Typer raises a usage error (exit 2) when a required argument
        # is missing.
        assert r.exit_code != 0

    def test_rebuild_unknown_service_exits_nonzero(
        self, tmp_path: Path
    ) -> None:
        _write_compose(tmp_path, {"mcp-server": {"image": "x"}})
        r = runner.invoke(
            app,
            ["rebuild", "bogus", "--install-dir", str(tmp_path)],
        )
        assert r.exit_code == 1
        assert "unknown service(s): bogus" in r.output
        assert "mcp-server" in r.output  # available list

    def test_rebuild_happy_path(self, tmp_path: Path) -> None:
        _write_compose(tmp_path, {"mcp-server": {"image": "x"}})
        with (
            patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/podman-compose"),
            patch("wip_deploy.apply.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            r = runner.invoke(
                app,
                [
                    "rebuild", "mcp-server",
                    "--install-dir", str(tmp_path),
                    "--no-wait",
                ],
            )
        assert r.exit_code == 0, r.output
        assert "Rebuilt mcp-server" in r.output
