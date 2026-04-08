"""Tests for wip_toolkit.import_.importer.run_import()."""

from unittest.mock import MagicMock, patch

import pytest
from wip_toolkit.models import EntityCounts, ImportStats


def _make_manifest(namespace="source-ns"):
    """Create a mock manifest."""
    manifest = MagicMock()
    manifest.namespace = namespace
    manifest.counts.total = 42
    return manifest


def _make_reader(manifest=None):
    """Create a mock ArchiveReader context manager."""
    reader = MagicMock()
    reader.__enter__ = MagicMock(return_value=reader)
    reader.__exit__ = MagicMock(return_value=False)
    reader.read_manifest.return_value = manifest or _make_manifest()
    return reader


def _healthy_services():
    """All-healthy service health dict."""
    return {
        "registry": (True, "healthy"),
        "def-store": (True, "healthy"),
        "template-store": (True, "healthy"),
        "document-store": (True, "healthy"),
    }


def _make_stats(mode="restore", namespace="source-ns"):
    """Create ImportStats as returned by restore/fresh sub-functions."""
    stats = ImportStats(mode=mode, target_namespace=namespace)
    stats.created = EntityCounts(terminologies=2, terms=10, templates=3, documents=20)
    return stats


# -- Patched module paths --
_ARCHIVE_READER = "wip_toolkit.import_.importer.ArchiveReader"
_RESTORE_IMPORT = "wip_toolkit.import_.importer.restore_import"
_FRESH_IMPORT = "wip_toolkit.import_.importer.fresh_import"


class TestRunImportDispatching:
    """run_import dispatches to the correct sub-function based on mode."""

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_restore_mode_dispatches_to_restore(
        self, MockReader, mock_restore, mock_fresh
    ):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader
        mock_restore.return_value = _make_stats("restore")

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        from wip_toolkit.import_.importer import run_import

        run_import(client, "/tmp/test.zip", mode="restore")

        mock_restore.assert_called_once()
        mock_fresh.assert_not_called()

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_fresh_mode_dispatches_to_fresh(
        self, MockReader, mock_restore, mock_fresh
    ):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader
        mock_fresh.return_value = _make_stats("fresh")

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        from wip_toolkit.import_.importer import run_import

        run_import(client, "/tmp/test.zip", mode="fresh")

        mock_fresh.assert_called_once()
        mock_restore.assert_not_called()

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_unknown_mode_raises(self, MockReader, mock_restore, mock_fresh):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        from wip_toolkit.import_.importer import run_import

        with pytest.raises(ValueError, match="Unknown import mode: other"):
            run_import(client, "/tmp/test.zip", mode="other")


class TestRunImportNamespace:
    """run_import resolves the target namespace correctly."""

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_target_namespace_override(
        self, MockReader, mock_restore, mock_fresh
    ):
        mock_reader = _make_reader(_make_manifest("source-ns"))
        MockReader.return_value = mock_reader
        mock_restore.return_value = _make_stats("restore", "override-ns")

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        from wip_toolkit.import_.importer import run_import

        run_import(client, "/tmp/test.zip", mode="restore", target_namespace="override-ns")

        args, _ = mock_restore.call_args
        # Second positional arg is the reader, third is namespace
        assert args[2] == "override-ns"

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_default_namespace_from_manifest(
        self, MockReader, mock_restore, mock_fresh
    ):
        mock_reader = _make_reader(_make_manifest("manifest-ns"))
        MockReader.return_value = mock_reader
        mock_restore.return_value = _make_stats("restore", "manifest-ns")

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        from wip_toolkit.import_.importer import run_import

        run_import(client, "/tmp/test.zip", mode="restore")

        args, _ = mock_restore.call_args
        assert args[2] == "manifest-ns"


class TestRunImportHealthCheck:
    """run_import checks service health before dispatching."""

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_unhealthy_services_aborts(
        self, MockReader, mock_restore, mock_fresh
    ):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader

        client = MagicMock()
        client.check_all_services.return_value = {
            "registry": (True, "healthy"),
            "def-store": (False, "connection refused"),
            "template-store": (True, "healthy"),
            "document-store": (True, "healthy"),
        }

        from wip_toolkit.import_.importer import run_import

        stats = run_import(client, "/tmp/test.zip", mode="restore")

        assert stats.errors == ["Service health check failed"]
        mock_restore.assert_not_called()
        mock_fresh.assert_not_called()

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_all_services_healthy(
        self, MockReader, mock_restore, mock_fresh
    ):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader
        mock_restore.return_value = _make_stats()

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        from wip_toolkit.import_.importer import run_import

        stats = run_import(client, "/tmp/test.zip", mode="restore")

        mock_restore.assert_called_once()
        assert stats.errors == []


class TestRunImportOptions:
    """run_import passes all keyword options to sub-functions."""

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_passes_all_options_to_restore(
        self, MockReader, mock_restore, mock_fresh
    ):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader
        mock_restore.return_value = _make_stats()

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        from wip_toolkit.import_.importer import run_import

        run_import(
            client, "/tmp/test.zip",
            mode="restore",
            skip_documents=True,
            skip_files=True,
            batch_size=200,
            continue_on_error=True,
            dry_run=True,
        )

        _, kwargs = mock_restore.call_args
        assert kwargs["skip_documents"] is True
        assert kwargs["skip_files"] is True
        assert kwargs["batch_size"] == 200
        assert kwargs["continue_on_error"] is True
        assert kwargs["dry_run"] is True

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_passes_all_options_to_fresh(
        self, MockReader, mock_restore, mock_fresh
    ):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader
        mock_fresh.return_value = _make_stats("fresh")

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        from wip_toolkit.import_.importer import run_import

        run_import(
            client, "/tmp/test.zip",
            mode="fresh",
            register_synonyms=True,
            skip_documents=True,
            skip_files=True,
            batch_size=100,
            continue_on_error=True,
            dry_run=True,
        )

        _, kwargs = mock_fresh.call_args
        assert kwargs["register_synonyms"] is True
        assert kwargs["skip_documents"] is True
        assert kwargs["skip_files"] is True
        assert kwargs["batch_size"] == 100
        assert kwargs["continue_on_error"] is True
        assert kwargs["dry_run"] is True


# ===========================================================================
# progress_callback (CASE-23 backup/restore prep)
# ===========================================================================
class TestRunImportProgressCallback:
    """Verify progress_callback observes phase boundaries and is fault-tolerant."""

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_callback_receives_start_and_complete(
        self, MockReader, mock_restore, mock_fresh,
    ):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader
        mock_restore.return_value = _make_stats()

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        events = []

        from wip_toolkit.import_.importer import run_import
        run_import(
            client, "/tmp/test.zip", mode="restore",
            progress_callback=events.append,
        )

        phases = [e.phase for e in events]
        assert phases[0] == "start"
        assert phases[-1] == "complete"
        assert events[0].percent == 0.0
        assert events[-1].percent == 100.0
        # 'complete' details should include the created counts dict
        assert "created" in events[-1].details
        assert events[-1].details["mode"] == "restore"

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_callback_observes_health_check_phase(
        self, MockReader, mock_restore, mock_fresh,
    ):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader
        mock_restore.return_value = _make_stats()

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        events = []

        from wip_toolkit.import_.importer import run_import
        run_import(
            client, "/tmp/test.zip", mode="restore",
            progress_callback=events.append,
        )

        phases = {e.phase for e in events}
        assert "phase_health_check" in phases

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_callback_emits_error_on_unhealthy_services(
        self, MockReader, mock_restore, mock_fresh,
    ):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader

        client = MagicMock()
        client.check_all_services.return_value = {
            "registry": (True, "healthy"),
            "def-store": (False, "connection refused"),
        }

        events = []

        from wip_toolkit.import_.importer import run_import
        stats = run_import(
            client, "/tmp/test.zip", mode="restore",
            progress_callback=events.append,
        )

        # Restore should not be invoked
        mock_restore.assert_not_called()
        assert stats.errors == ["Service health check failed"]
        # An error event should have been emitted
        error_events = [e for e in events if e.phase == "error"]
        assert len(error_events) == 1
        assert "def-store" in error_events[0].details["health"]

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_callback_passed_through_to_restore(
        self, MockReader, mock_restore, mock_fresh,
    ):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader
        mock_restore.return_value = _make_stats()

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        cb = lambda e: None  # noqa: E731

        from wip_toolkit.import_.importer import run_import
        run_import(
            client, "/tmp/test.zip", mode="restore",
            progress_callback=cb,
        )

        _, kwargs = mock_restore.call_args
        assert kwargs["progress_callback"] is cb

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_callback_passed_through_to_fresh(
        self, MockReader, mock_restore, mock_fresh,
    ):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader
        mock_fresh.return_value = _make_stats("fresh")

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        cb = lambda e: None  # noqa: E731

        from wip_toolkit.import_.importer import run_import
        run_import(
            client, "/tmp/test.zip", mode="fresh",
            progress_callback=cb,
        )

        _, kwargs = mock_fresh.call_args
        assert kwargs["progress_callback"] is cb

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_callback_exception_does_not_break_import(
        self, MockReader, mock_restore, mock_fresh,
    ):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader
        mock_restore.return_value = _make_stats()

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        def explosive(_event):
            raise RuntimeError("observer is broken")

        from wip_toolkit.import_.importer import run_import
        # Must complete normally despite the callback raising on every event
        stats = run_import(
            client, "/tmp/test.zip", mode="restore",
            progress_callback=explosive,
        )

        assert stats.mode == "restore"
        mock_restore.assert_called_once()

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_no_callback_is_supported(
        self, MockReader, mock_restore, mock_fresh,
    ):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader
        mock_restore.return_value = _make_stats()

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        from wip_toolkit.import_.importer import run_import
        # Default (None) callback must not raise
        stats = run_import(client, "/tmp/test.zip", mode="restore")
        assert stats.mode == "restore"

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_non_interactive_accepted(
        self, MockReader, mock_restore, mock_fresh,
    ):
        """non_interactive is accepted for parity with run_export."""
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader
        mock_restore.return_value = _make_stats()

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        from wip_toolkit.import_.importer import run_import
        # Should accept the parameter without raising
        stats = run_import(
            client, "/tmp/test.zip", mode="restore",
            non_interactive=True,
        )
        assert stats.mode == "restore"


class TestRunImportStats:
    """run_import returns ImportStats with correct metadata."""

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_duration_set(self, MockReader, mock_restore, mock_fresh):
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader
        mock_restore.return_value = _make_stats()

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        from wip_toolkit.import_.importer import run_import

        stats = run_import(client, "/tmp/test.zip", mode="restore")

        assert isinstance(stats.duration_seconds, float)
        assert stats.duration_seconds >= 0

    @patch(_FRESH_IMPORT)
    @patch(_RESTORE_IMPORT)
    @patch(_ARCHIVE_READER)
    def test_summary_printed(self, MockReader, mock_restore, mock_fresh):
        """Smoke test: summary printing does not raise."""
        mock_reader = _make_reader()
        MockReader.return_value = mock_reader

        stats = _make_stats()
        stats.skipped = EntityCounts(terms=5)
        stats.failed = EntityCounts(documents=1)
        stats.id_mappings = 3
        stats.synonyms_registered = 2
        stats.errors = ["error 1"]
        stats.warnings = ["warning 1"]
        mock_restore.return_value = stats

        client = MagicMock()
        client.check_all_services.return_value = _healthy_services()

        from wip_toolkit.import_.importer import run_import

        result = run_import(client, "/tmp/test.zip", mode="restore")

        # If we get here, printing did not raise
        assert result.mode == "restore"
