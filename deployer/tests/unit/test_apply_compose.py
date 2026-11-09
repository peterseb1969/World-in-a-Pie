"""Tests for compose-specific apply helpers.

Currently focused on `_remove_install_containers`: before a full up, the
install's own containers (found by compose project label) are removed: all
of them on the dev target (CASE-282, every service on a fresh image), and
those the render does not name on the compose target (a removed service,
or the fixed `wip-<svc>` names every install had before per-install naming,
CASE-1225). Containers of another install are never listed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from wip_deploy.apply import _remove_install_containers

_PODMAN = {"side_effect": lambda name: "/usr/bin/podman" if name == "podman" else None}


class TestRemoveInstallContainers:
    @patch("wip_deploy.apply.shutil.which", return_value=None)
    def test_no_runtime_is_silent_noop(self, _which: MagicMock) -> None:
        _remove_install_containers("pi-a", keep=None)  # no raise

    @patch("wip_deploy.apply.subprocess.run")
    @patch("wip_deploy.apply.shutil.which", **_PODMAN)
    def test_lists_only_this_installs_project(
        self, _which: MagicMock, mock_run: MagicMock
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        _remove_install_containers("pi-a", keep=None)
        assert mock_run.call_count == 1  # the ps; nothing to remove
        cmd = mock_run.call_args_list[0].args[0]
        assert cmd[:3] == ["podman", "ps", "-a"]
        assert "label=com.docker.compose.project=pi-a" in cmd
        assert not any(str(arg).startswith("name=") for arg in cmd)

    @patch("wip_deploy.apply.subprocess.run")
    @patch("wip_deploy.apply.shutil.which", **_PODMAN)
    def test_dev_removes_every_container_of_the_install(
        self, _which: MagicMock, mock_run: MagicMock
    ) -> None:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="pi-a_wip-registry\npi-a_wip-mongodb\nwip-postgres\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        _remove_install_containers("pi-a", keep=None)
        rm_cmd = mock_run.call_args_list[1].args[0]
        assert rm_cmd[:3] == ["podman", "rm", "-f"]
        assert set(rm_cmd[3:]) == {"pi-a_wip-registry", "pi-a_wip-mongodb", "wip-postgres"}

    @patch("wip_deploy.apply.subprocess.run")
    @patch("wip_deploy.apply.shutil.which", **_PODMAN)
    def test_compose_keeps_what_the_render_names(
        self, _which: MagicMock, mock_run: MagicMock
    ) -> None:
        """A legacy fixed-name container of the install goes; the rendered
        ones stay for compose up to reconcile."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="default_wip-registry\nwip-registry\nwip-caddy\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        _remove_install_containers("default", keep={"default_wip-registry", "default_wip-caddy"})
        rm_cmd = mock_run.call_args_list[1].args[0]
        assert set(rm_cmd[3:]) == {"wip-registry", "wip-caddy"}

    @patch("wip_deploy.apply.subprocess.run")
    @patch("wip_deploy.apply.shutil.which", **_PODMAN)
    def test_nothing_outside_keep_means_no_rm(
        self, _which: MagicMock, mock_run: MagicMock
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="pi-a_wip-registry\n", stderr="")
        _remove_install_containers("pi-a", keep={"pi-a_wip-registry"})
        assert mock_run.call_count == 1

    @patch("wip_deploy.apply.subprocess.run")
    @patch(
        "wip_deploy.apply.shutil.which",
        side_effect=lambda name: (
            None if name == "podman" else "/usr/bin/docker" if name == "docker" else None
        ),
    )
    def test_falls_back_to_docker(
        self, _which: MagicMock, mock_run: MagicMock
    ) -> None:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="pi-a_wip-registry\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        _remove_install_containers("pi-a", keep=None)
        for call in mock_run.call_args_list:
            assert call.args[0][0] == "docker"

    @patch("wip_deploy.apply.subprocess.run")
    @patch("wip_deploy.apply.shutil.which", **_PODMAN)
    def test_ps_failure_is_silent_noop(
        self, _which: MagicMock, mock_run: MagicMock
    ) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="connection refused")
        _remove_install_containers("pi-a", keep=None)
        assert mock_run.call_count == 1
