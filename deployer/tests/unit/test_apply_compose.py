"""Tests for compose-specific apply helpers.

Currently focused on `_remove_stale_wip_containers` (CASE-282) — the
dev-target pre-flight that wipes stale wip-* containers before
`compose up` so cross-project name conflicts can't leave the operator
with a mix of fresh and stale state.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from wip_deploy.apply import _remove_stale_wip_containers


class TestRemoveStaleWipContainers:
    @patch("wip_deploy.apply.shutil.which", return_value=None)
    def test_no_runtime_is_silent_noop(self, _which: MagicMock) -> None:
        # Neither podman nor docker on PATH — function should return
        # without raising, letting compose up surface the real error.
        _remove_stale_wip_containers()  # no raise

    @patch("wip_deploy.apply.subprocess.run")
    @patch(
        "wip_deploy.apply.shutil.which",
        side_effect=lambda name: "/usr/bin/podman" if name == "podman" else None,
    )
    def test_no_wip_containers_skips_rm(
        self, _which: MagicMock, mock_run: MagicMock
    ) -> None:
        # Empty `ps` output → no rm call.
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        _remove_stale_wip_containers()
        # Exactly one call: the ps. No rm.
        assert mock_run.call_count == 1
        cmd = mock_run.call_args_list[0].args[0]
        assert cmd[:3] == ["podman", "ps", "-a"]

    @patch("wip_deploy.apply.subprocess.run")
    @patch(
        "wip_deploy.apply.shutil.which",
        side_effect=lambda name: "/usr/bin/podman" if name == "podman" else None,
    )
    def test_lists_then_rm_minus_f(
        self, _which: MagicMock, mock_run: MagicMock
    ) -> None:
        # ps returns three wip-* containers → one rm -f call with all of them.
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout="wip-registry\nwip-mongodb\nwip-postgres\n",
                stderr="",
            ),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        _remove_stale_wip_containers()
        assert mock_run.call_count == 2
        rm_cmd = mock_run.call_args_list[1].args[0]
        assert rm_cmd[0] == "podman"
        assert rm_cmd[1:3] == ["rm", "-f"]
        assert set(rm_cmd[3:]) == {"wip-registry", "wip-mongodb", "wip-postgres"}

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
        # podman missing, docker present → uses docker for both ps and rm.
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="wip-registry\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        _remove_stale_wip_containers()
        for call in mock_run.call_args_list:
            assert call.args[0][0] == "docker"

    @patch("wip_deploy.apply.subprocess.run")
    @patch(
        "wip_deploy.apply.shutil.which",
        side_effect=lambda name: "/usr/bin/podman" if name == "podman" else None,
    )
    def test_ps_failure_is_silent_noop(
        self, _which: MagicMock, mock_run: MagicMock
    ) -> None:
        # ps returning non-zero (daemon down, etc.) → no rm. The
        # subsequent compose up will produce the real error message.
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="connection refused")
        _remove_stale_wip_containers()
        # Only the ps call, no rm.
        assert mock_run.call_count == 1
