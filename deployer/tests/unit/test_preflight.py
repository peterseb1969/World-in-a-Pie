"""Tests for the preflight module."""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wip_deploy.preflight import (
    PreflightError,
    check_no_stale_containers,
    check_port_free,
    check_ports_free,
)

# ────────────────────────────────────────────────────────────────────
# Port checks
# ────────────────────────────────────────────────────────────────────


def _find_busy_port() -> int:
    """Bind a real socket on localhost and return its port.

    Caller is responsible for keeping the socket alive during the test.
    Returns a port guaranteed to be in use.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock.getsockname()[1], sock


class TestPortChecks:
    def test_free_port_passes(self) -> None:
        # Bind a socket, get its port, release it. Port is free now.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        # Should not raise.
        check_port_free(port, host="127.0.0.1")

    def test_bound_port_raises(self) -> None:
        port, sock = _find_busy_port()
        try:
            with pytest.raises(PreflightError, match=f"port {port}"):
                check_port_free(port, host="127.0.0.1")
        finally:
            sock.close()

    def test_error_message_points_at_nuke(self) -> None:
        port, sock = _find_busy_port()
        try:
            with pytest.raises(PreflightError, match="wip-deploy nuke"):
                check_port_free(port, host="127.0.0.1")
        finally:
            sock.close()

    def test_multiple_ports_checked(self) -> None:
        port, sock = _find_busy_port()
        try:
            with pytest.raises(PreflightError, match=f"port {port}"):
                check_ports_free([9999, port], host="127.0.0.1")
        finally:
            sock.close()

    def test_reconcile_skips_port_check_when_install_exists(
        self, tmp_path: Path
    ) -> None:
        """CASE-171 #3: re-installing the same name shouldn't refuse
        because our own caddy container is holding the port. When the
        install_dir contains a rendered compose, we're reconciling —
        let compose handle port reuse."""
        (tmp_path / "docker-compose.yaml").write_text("services: {}\n")
        port, sock = _find_busy_port()
        try:
            # Should NOT raise — install_dir presence flips us to reconcile.
            check_ports_free([port], host="127.0.0.1", install_dir=tmp_path)
        finally:
            sock.close()

    def test_reconcile_still_checks_when_install_dir_empty(
        self, tmp_path: Path
    ) -> None:
        """An install_dir without docker-compose.yaml is fresh-install
        — the normal port check still runs."""
        port, sock = _find_busy_port()
        try:
            with pytest.raises(PreflightError, match=f"port {port}"):
                check_ports_free([port], host="127.0.0.1", install_dir=tmp_path)
        finally:
            sock.close()


# ────────────────────────────────────────────────────────────────────
# Stale container checks
# ────────────────────────────────────────────────────────────────────


class TestStaleContainerChecks:
    def test_no_warning_when_install_dir_already_has_compose(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "docker-compose.yaml").write_text("services: {}\n")
        # Even if containers exist, we're on the reinstall path — no warn.
        with patch("wip_deploy.preflight._container_runtime", return_value="podman"):
            warnings = check_no_stale_containers(tmp_path)
        assert warnings == []

    def test_no_warning_when_no_runtime(self, tmp_path: Path) -> None:
        with patch("wip_deploy.preflight._container_runtime", return_value=None):
            warnings = check_no_stale_containers(tmp_path)
        assert warnings == []

    def test_no_warning_when_no_stray_containers(self, tmp_path: Path) -> None:
        with patch("wip_deploy.preflight._container_runtime", return_value="podman"), \
             patch("wip_deploy.preflight.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="")
            warnings = check_no_stale_containers(tmp_path)
        assert warnings == []

    def test_warns_on_stray_containers(self, tmp_path: Path) -> None:
        """When install_dir has no compose.yaml but wip-* containers
        exist on the host, warn."""
        with patch("wip_deploy.preflight._container_runtime", return_value="podman"), \
             patch("wip_deploy.preflight.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="wip-caddy\nwip-registry\nwip-mongodb\n"
            )
            warnings = check_no_stale_containers(tmp_path)

        assert len(warnings) == 1
        msg = warnings[0].message
        assert "3 stray" in msg
        assert "wip-caddy" in msg
        assert "nuke" in msg

    def test_truncates_long_container_list(self, tmp_path: Path) -> None:
        names = [f"wip-thing-{i}" for i in range(10)]
        with patch("wip_deploy.preflight._container_runtime", return_value="podman"), \
             patch("wip_deploy.preflight.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="\n".join(names))
            warnings = check_no_stale_containers(tmp_path)

        assert len(warnings) == 1
        assert "10 stray" in warnings[0].message
        assert "and 5 more" in warnings[0].message
