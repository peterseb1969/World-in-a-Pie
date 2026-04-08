"""Tests for the loopback WIPConfig + runner factories (CASE-23 Phase 3 STEP 4).

These are unit-level tests. The toolkit itself is not exercised end-to-end
here (that's the STEP 6 integration smoke test); what we verify is:

* ``_loopback_config`` reads ``WIP_AUTH_LEGACY_API_KEY`` from the environment
* ``_loopback_config`` raises when the env var is unset and no override given
* The returned ``WIPConfig`` is in direct (non-proxy) mode and produces
  ``http://localhost:{port}`` URLs for every required service
* ``make_backup_runner`` and ``make_restore_runner`` return callables that,
  when invoked, call the underlying toolkit functions with the supplied
  namespace/archive + propagate options and the progress_callback
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from wip_toolkit.models import ProgressEvent

from document_store.services import backup_service


@pytest.fixture(autouse=True)
def _clear_container_mode_env(monkeypatch):
    """Ensure loopback tests default to host mode.

    Document-store's compose file sets REGISTRY_URL (and friends), which
    _loopback_config treats as a signal to switch to container-mode URLs.
    These unit tests assert host-mode behavior, so strip those vars unless
    an individual test sets them back.
    """
    for var in (
        "REGISTRY_URL",
        "DEF_STORE_URL",
        "TEMPLATE_STORE_URL",
        "DOCUMENT_STORE_URL",
        "REPORTING_SYNC_URL",
        "INGEST_GATEWAY_URL",
    ):
        monkeypatch.delenv(var, raising=False)


class TestLoopbackConfig:
    def test_uses_env_var_when_no_override(self, monkeypatch):
        monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "env-key-123")
        cfg = backup_service._loopback_config()
        assert cfg.api_key == "env-key-123"
        assert cfg.host == "localhost"
        assert cfg.proxy is False

    def test_override_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "env-key")
        cfg = backup_service._loopback_config(api_key="override-key")
        assert cfg.api_key == "override-key"

    def test_raises_when_no_key_available(self, monkeypatch):
        monkeypatch.delenv("WIP_AUTH_LEGACY_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="WIP_AUTH_LEGACY_API_KEY"):
            backup_service._loopback_config()

    def test_direct_urls_for_all_services(self, monkeypatch):
        monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "k")
        cfg = backup_service._loopback_config()
        # Host mode: every service reachable via http://localhost:{port}{prefix}
        assert cfg.service_url("registry") == "http://localhost:8001/api/registry"
        assert cfg.service_url("def-store") == "http://localhost:8002/api/def-store"
        assert (
            cfg.service_url("template-store")
            == "http://localhost:8003/api/template-store"
        )
        assert (
            cfg.service_url("document-store")
            == "http://localhost:8004/api/document-store"
        )

    def test_container_mode_uses_per_service_hostnames(self, monkeypatch):
        """When REGISTRY_URL is set, switch to per-service in-network URLs."""
        monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "k")
        monkeypatch.setenv("REGISTRY_URL", "http://wip-registry:8001")
        monkeypatch.setenv("DEF_STORE_URL", "http://wip-def-store:8002")
        monkeypatch.setenv("TEMPLATE_STORE_URL", "http://wip-template-store:8003")
        cfg = backup_service._loopback_config()
        assert (
            cfg.service_url("registry") == "http://wip-registry:8001/api/registry"
        )
        assert (
            cfg.service_url("def-store") == "http://wip-def-store:8002/api/def-store"
        )
        assert (
            cfg.service_url("template-store")
            == "http://wip-template-store:8003/api/template-store"
        )


class TestBackupRunnerFactory:
    def test_runner_calls_run_export_with_expected_args(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "k")
        captured: dict[str, Any] = {}

        def fake_run_export(client, namespace, output_path, **kwargs):
            captured["client_type"] = type(client).__name__
            captured["namespace"] = namespace
            captured["output_path"] = output_path
            captured["kwargs"] = kwargs
            return "export-stats"

        with patch.object(backup_service, "run_export", fake_run_export):
            runner = backup_service.make_backup_runner(
                "wip",
                tmp_path / "backup.zip",
                options={"include_files": True, "latest_only": True},
            )
            # Supply a no-op progress callback
            result = runner(lambda event: None)

        assert result == "export-stats"
        assert captured["client_type"] == "WIPClient"
        assert captured["namespace"] == "wip"
        assert captured["output_path"] == tmp_path / "backup.zip"
        assert captured["kwargs"]["include_files"] is True
        assert captured["kwargs"]["latest_only"] is True
        assert captured["kwargs"]["non_interactive"] is True
        assert callable(captured["kwargs"]["progress_callback"])

    def test_runner_passes_wip_backup_dir_as_tmp_dir(self, monkeypatch, tmp_path):
        """The runner threads WIP_BACKUP_DIR through to run_export's tmp_dir.

        CASE-29: scratch storage must live under the same operator-controlled
        volume as the final archive, not the system /tmp default.
        """
        monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "k")
        scratch = tmp_path / "wip-backups-scratch"
        monkeypatch.setenv("WIP_BACKUP_DIR", str(scratch))

        captured: dict[str, Any] = {}

        def fake_run_export(client, namespace, output_path, **kwargs):
            captured["kwargs"] = kwargs

        with patch.object(backup_service, "run_export", fake_run_export):
            runner = backup_service.make_backup_runner("wip", tmp_path / "b.zip")
            runner(lambda e: None)

        assert captured["kwargs"]["tmp_dir"] == str(scratch)
        # Factory should also have created the directory.
        assert scratch.is_dir()

    def test_runner_forwards_progress_callback(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "k")
        received: list[ProgressEvent] = []

        def fake_run_export(client, namespace, output_path, **kwargs):
            cb = kwargs["progress_callback"]
            cb(ProgressEvent(phase="start", message="go", percent=0.0))
            cb(ProgressEvent(phase="complete", message="done", percent=100.0))
            return None

        with patch.object(backup_service, "run_export", fake_run_export):
            runner = backup_service.make_backup_runner("wip", tmp_path / "b.zip")
            runner(received.append)

        assert [e.phase for e in received] == ["start", "complete"]


class TestRestoreRunnerFactory:
    def test_runner_calls_run_import_with_expected_args(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "k")
        captured: dict[str, Any] = {}

        def fake_run_import(client, archive_path, **kwargs):
            captured["client_type"] = type(client).__name__
            captured["archive_path"] = archive_path
            captured["kwargs"] = kwargs
            return "import-stats"

        archive = tmp_path / "restore.zip"
        with patch.object(backup_service, "run_import", fake_run_import):
            runner = backup_service.make_restore_runner(
                archive,
                options={
                    "mode": "fresh",
                    "target_namespace": "wip-copy",
                    "register_synonyms": True,
                },
            )
            result = runner(lambda event: None)

        assert result == "import-stats"
        assert captured["client_type"] == "WIPClient"
        assert captured["archive_path"] == archive
        assert captured["kwargs"]["mode"] == "fresh"
        assert captured["kwargs"]["target_namespace"] == "wip-copy"
        assert captured["kwargs"]["register_synonyms"] is True
        assert captured["kwargs"]["non_interactive"] is True
        assert callable(captured["kwargs"]["progress_callback"])

    def test_options_none_defaults_to_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "k")
        captured: dict[str, Any] = {}

        def fake_run_import(client, archive_path, **kwargs):
            captured["kwargs"] = kwargs

        with patch.object(backup_service, "run_import", fake_run_import):
            runner = backup_service.make_restore_runner(tmp_path / "a.zip")
            runner(lambda e: None)

        # The factory injects three kwargs by default: progress_callback,
        # non_interactive, and tmp_dir (CASE-29 — co-locates scratch with
        # the configured WIP_BACKUP_DIR volume).
        assert set(captured["kwargs"].keys()) == {
            "progress_callback", "non_interactive", "tmp_dir",
        }
