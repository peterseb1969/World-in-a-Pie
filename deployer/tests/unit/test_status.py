"""Tests for the status module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wip_deploy.status import (
    ServiceStatus,
    StatusError,
    format_table,
    read_compose_status,
    read_k8s_status,
)

# ────────────────────────────────────────────────────────────────────
# format_table
# ────────────────────────────────────────────────────────────────────


class TestFormatTable:
    def test_empty_rows_returns_friendly_message(self) -> None:
        assert format_table([]) == "(no services found)"

    def test_renders_header_and_rows(self) -> None:
        rows = [
            ServiceStatus(name="wip-registry", state="running", health="healthy"),
            ServiceStatus(name="wip-dex", state="running", health=""),
        ]
        out = format_table(rows)
        lines = out.splitlines()
        assert lines[0].startswith("NAME")
        assert "STATE" in lines[0]
        assert "HEALTH" in lines[0]
        # No-probe services render as "—"
        assert "—" in out
        # Both services appear
        assert any("wip-registry" in line for line in lines)
        assert any("wip-dex" in line for line in lines)


# ────────────────────────────────────────────────────────────────────
# read_compose_status
# ────────────────────────────────────────────────────────────────────


class TestComposeStatus:
    def test_missing_compose_file_errors(self, tmp_path: Path) -> None:
        with pytest.raises(StatusError, match="not a compose install"):
            read_compose_status(tmp_path)

    def test_splits_state_from_health(self, tmp_path: Path) -> None:
        (tmp_path / "docker-compose.yaml").write_text("services: {}\n")
        with patch("wip_deploy.status._detect_compose_cmd", return_value=["podman-compose"]), \
             patch("wip_deploy.status._compose_ps") as mock_ps:
            mock_ps.return_value = {
                "wip-registry": "healthy",
                "wip-def-store": "starting",
                "wip-dex": "running",  # no probe → state folded into health
                "wip-old": "exited",
            }
            rows = read_compose_status(tmp_path)

        by_name = {r.name: r for r in rows}
        assert by_name["wip-registry"].state == "running"
        assert by_name["wip-registry"].health == "healthy"
        assert by_name["wip-dex"].state == "running"
        assert by_name["wip-dex"].health == ""  # no probe → empty
        assert by_name["wip-old"].state == "exited"


# ────────────────────────────────────────────────────────────────────
# read_k8s_status
# ────────────────────────────────────────────────────────────────────


class TestK8sStatus:
    def _kubectl_json(self, pods: list[dict]) -> str:
        return json.dumps({"items": pods})

    def test_returns_empty_for_empty_namespace(self) -> None:
        with patch("wip_deploy.status.shutil.which", return_value="/usr/bin/kubectl"), \
             patch("wip_deploy.status.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=self._kubectl_json([]))
            rows = read_k8s_status("wip")
        assert rows == []

    def test_parses_running_ready_pod(self) -> None:
        pod = {
            "metadata": {"name": "wip-registry-abc123"},
            "status": {
                "phase": "Running",
                "conditions": [{"type": "Ready", "status": "True"}],
            },
        }
        with patch("wip_deploy.status.shutil.which", return_value="/usr/bin/kubectl"), \
             patch("wip_deploy.status.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=self._kubectl_json([pod]))
            rows = read_k8s_status("wip")

        assert len(rows) == 1
        assert rows[0].name == "wip-registry-abc123"
        assert rows[0].state == "running"
        assert rows[0].health == "healthy"

    def test_parses_pending_pod(self) -> None:
        pod = {
            "metadata": {"name": "wip-dex-xyz"},
            "status": {
                "phase": "Pending",
                "conditions": [{"type": "Ready", "status": "False"}],
            },
        }
        with patch("wip_deploy.status.shutil.which", return_value="/usr/bin/kubectl"), \
             patch("wip_deploy.status.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=self._kubectl_json([pod]))
            rows = read_k8s_status("wip")

        assert rows[0].state == "pending"
        assert rows[0].health == "not ready"

    def test_errors_when_kubectl_missing(self) -> None:
        with patch("wip_deploy.status.shutil.which", return_value=None), \
             pytest.raises(StatusError, match="kubectl not on PATH"):
            read_k8s_status("wip")
