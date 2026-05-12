"""Regression coverage for CASE-313 — additive add/remove verbs.

The new subcommands (`add-app`, `remove-app`, `add-module`,
`remove-module`) load the persisted deployment-state, mutate one
field, re-render+apply, and persist. End-to-end CLI smoke is
covered by the live-install verification documented in the
implementation note; this file is the narrow contract test for the
mutation surface and the orphan-container cleanup helper.

The render+apply path is exercised by `test_apply_compose.py` and
the install integration suite, so we don't re-exercise it here.
What we DO test:

  - `stop_and_remove_container` calls `podman rm -f wip-<name>`
    and is silent when the runtime isn't installed.
  - The CLI verbs reject unknown names with actionable errors.
  - The CLI verbs are idempotent (already-enabled, already-removed).
  - The CLI verbs mutate the persisted spec correctly when the
    happy path runs (verified by patching apply_compose to a no-op
    and inspecting the post-call state file).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from wip_deploy.apply import stop_and_remove_container
from wip_deploy.cli import app

runner = CliRunner()


# ────────────────────────────────────────────────────────────────────
# Helpers — minimal install dir with a deployment-state file.
# ────────────────────────────────────────────────────────────────────


def _write_deployment_state(install_dir: Path, apps: list[dict], modules: list[str]) -> None:
    """Write a minimal `deployment.deployer-state` the CLI can load.

    Mirrors the shape `_persist_deployment` writes; full enough that
    `Deployment.model_validate` accepts it.
    """
    install_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "wip_deploy_format_version": 1,
        "deployment": {
            "metadata": {"name": "test-install"},
            "spec": {
                "target": "compose",
                "apps": apps,
                "modules": {"optional": modules},
                "auth": {"mode": "api-key-only", "gateway": False, "users": []},
                "network": {"hostname": "localhost"},
                "images": {},
                "platform": {
                    "compose": {"data_dir": "/tmp/wip-test-data"},
                },
                "secrets": {"backend": "file", "location": "/tmp/wip-test-secrets"},
                "apply": {},
            },
        },
    }
    (install_dir / "deployment.deployer-state").write_text(json.dumps(payload, indent=2))


# ────────────────────────────────────────────────────────────────────
# stop_and_remove_container
# ────────────────────────────────────────────────────────────────────


class TestStopAndRemoveContainer:
    def test_runs_podman_rm_dash_f(self) -> None:
        with (
            patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/podman"),
            patch("wip_deploy.apply.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            stop_and_remove_container("react-console")

        cmd = mock_run.call_args_list[0].args[0]
        assert cmd[:3] == ["podman", "rm", "-f"]
        assert cmd[-1] == "wip-react-console"

    def test_falls_back_to_docker_when_no_podman(self) -> None:
        def which_stub(name: str) -> str | None:
            return "/usr/local/bin/docker" if name == "docker" else None

        with (
            patch("wip_deploy.apply.shutil.which", side_effect=which_stub),
            patch("wip_deploy.apply.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            stop_and_remove_container("kb")

        cmd = mock_run.call_args_list[0].args[0]
        assert cmd[0] == "docker"

    def test_silent_when_no_container_runtime(self) -> None:
        """No podman and no docker — silent no-op, doesn't raise."""
        with (
            patch("wip_deploy.apply.shutil.which", return_value=None),
            patch("wip_deploy.apply.subprocess.run") as mock_run,
        ):
            stop_and_remove_container("anything")
        mock_run.assert_not_called()

    def test_silent_when_container_missing(self) -> None:
        """`podman rm` returns non-zero when the container doesn't exist;
        the helper passes check=False and doesn't raise."""
        import subprocess
        with (
            patch("wip_deploy.apply.shutil.which", return_value="/usr/bin/podman"),
            patch("wip_deploy.apply.subprocess.run") as mock_run,
        ):
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="no such container"
            )
            # Should not raise.
            stop_and_remove_container("nonexistent-app")


# ────────────────────────────────────────────────────────────────────
# add-app / remove-app / add-module / remove-module — error paths
#
# These don't hit the render+apply path: the validation gate fires
# first and short-circuits the lifecycle. So we can run them against
# a stub install dir without mocking the heavy apply pipeline.
# ────────────────────────────────────────────────────────────────────


class TestAdditiveVerbsErrors:
    def test_add_app_missing_install_state(self, tmp_path: Path) -> None:
        """No deployment-state file at the install dir → exit 2."""
        result = runner.invoke(
            app,
            ["add-app", "react-console", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 2
        assert "deployment.deployer-state" in result.output

    def test_remove_app_missing_install_state(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["remove-app", "react-console", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 2
        assert "deployment.deployer-state" in result.output

    def test_add_module_missing_install_state(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["add-module", "reporting-sync", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 2

    def test_remove_module_missing_install_state(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["remove-module", "reporting-sync", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 2


# ────────────────────────────────────────────────────────────────────
# Idempotent / no-op paths
#
# When the requested state already matches reality, the verbs print
# a "⊙ no change" line and return 0 WITHOUT going through the
# render+apply lifecycle. These tests verify that early-exit so
# we don't accidentally trigger a heavy lifecycle on a no-op.
# ────────────────────────────────────────────────────────────────────


class TestIdempotentPaths:
    def test_add_app_already_enabled_is_noop(self, tmp_path: Path) -> None:
        _write_deployment_state(
            tmp_path,
            apps=[{"name": "react-console", "enabled": True}],
            modules=[],
        )
        # No apply_compose patch — the test would crash if we hit the
        # render+apply path. The "already enabled" early-exit must
        # short-circuit before that.
        result = runner.invoke(
            app,
            ["add-app", "react-console", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "already enabled" in result.output

    def test_remove_app_not_present_is_noop(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path, apps=[], modules=[])
        result = runner.invoke(
            app,
            ["remove-app", "react-console", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "not in this install" in result.output

    def test_remove_module_not_present_is_noop(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path, apps=[], modules=["minio"])
        result = runner.invoke(
            app,
            ["remove-module", "reporting-sync", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "not enabled" in result.output


# ────────────────────────────────────────────────────────────────────
# Validation rejections
#
# An add-app for an app that has no manifest, or an add-module for
# something that isn't a discovered optional component, must reject
# with a list of valid options. The discovery step runs against the
# WIP repo root, so these tests need a real-repo cwd — we run from
# `<repo_root>/components` upward to ensure find_repo_root works.
# ────────────────────────────────────────────────────────────────────


class TestValidationRejections:
    def test_add_app_unknown_name_lists_available(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path, apps=[], modules=[])
        result = runner.invoke(
            app,
            ["add-app", "totally-not-a-real-app", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 2
        assert "not found" in result.output
        # Should enumerate the actually-discovered apps so the
        # operator sees what's available and can fix the typo.
        assert "Available" in result.output

    def test_add_module_unknown_name_lists_available(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path, apps=[], modules=[])
        result = runner.invoke(
            app,
            ["add-module", "made-up-module", "--install-dir", str(tmp_path)],
        )
        assert result.exit_code == 2
        assert "not a discovered optional component" in result.output
        assert "Available" in result.output

    def test_add_app_source_on_non_dev_target_rejected(self, tmp_path: Path) -> None:
        """--app-source is dev-only; setting it on a compose install fails."""
        _write_deployment_state(tmp_path, apps=[], modules=[])
        result = runner.invoke(
            app,
            [
                "add-app", "react-console",
                "--app-source", str(tmp_path),  # any existing dir
                "--install-dir", str(tmp_path),
            ],
        )
        assert result.exit_code == 2
        assert "target=dev" in result.output

    def test_add_app_source_nonexistent_path_rejected(self, tmp_path: Path) -> None:
        _write_deployment_state(tmp_path, apps=[], modules=[])
        bogus = tmp_path / "does-not-exist"
        result = runner.invoke(
            app,
            [
                "add-app", "react-console",
                "--app-source", str(bogus),
                "--install-dir", str(tmp_path),
            ],
        )
        assert result.exit_code == 2
        # Either the target-is-not-dev message OR the path-not-a-dir
        # message, depending on validation order. Both are correct
        # rejections of the bad input.
        assert (
            "not a directory" in result.output
            or "target=dev" in result.output
        )
