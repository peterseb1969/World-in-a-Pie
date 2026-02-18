"""Tests for the Click CLI commands in wip_toolkit.cli."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from wip_toolkit.models import EntityCounts, ExportStats, ImportStats


def _healthy_services():
    """All-healthy service health dict."""
    return {
        "registry": (True, "OK"),
        "def-store": (True, "OK"),
        "template-store": (True, "OK"),
        "document-store": (True, "OK"),
    }


def _make_mock_client(healthy=True):
    """Create a mock WIPClient that works as a context manager."""
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    if healthy:
        client.check_all_services.return_value = _healthy_services()
    else:
        client.check_all_services.return_value = {
            "registry": (True, "OK"),
            "def-store": (False, "connection refused"),
            "template-store": (True, "OK"),
            "document-store": (True, "OK"),
        }
    return client


def _make_manifest():
    """Create a mock manifest for inspect tests."""
    manifest = MagicMock()
    manifest.format_version = "1.1"
    manifest.tool_version = "0.1.0"
    manifest.exported_at = "2025-01-01T00:00:00Z"
    manifest.source_host = "localhost"
    manifest.namespace = "wip"
    manifest.include_inactive = False
    manifest.include_files = False
    manifest.counts = MagicMock()
    manifest.counts.total = 10
    manifest.counts.terminologies = 2
    manifest.counts.terms = 5
    manifest.counts.templates = 2
    manifest.counts.documents = 1
    manifest.counts.files = 0
    manifest.closure = MagicMock()
    manifest.closure.external_terminologies = []
    manifest.closure.external_templates = []
    manifest.closure.warnings = []
    return manifest


def _make_mock_archive_reader(manifest=None):
    """Create a mock ArchiveReader context manager for inspect."""
    reader = MagicMock()
    reader.__enter__ = MagicMock(return_value=reader)
    reader.__exit__ = MagicMock(return_value=False)
    reader.read_manifest.return_value = manifest or _make_manifest()
    reader.entity_count.return_value = 0
    reader.list_blobs.return_value = []
    reader.compressed_size.return_value = 1024
    reader.total_size.return_value = 4096
    return reader


class TestMainGroup:
    """Tests for the CLI main group."""

    def test_help_output(self):
        from wip_toolkit.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["--help"])

        assert result.exit_code == 0
        assert "WIP Toolkit" in result.output
        assert "export" in result.output
        assert "import" in result.output
        assert "inspect" in result.output

    def test_group_is_accessible(self):
        from wip_toolkit.cli import main

        runner = CliRunner()
        # Invoking without a subcommand shows help
        result = runner.invoke(main, [])

        assert result.exit_code == 0 or result.exit_code == 2
        assert "Usage" in result.output


class TestExportCommand:
    """Tests for the export CLI command."""

    @patch("wip_toolkit.cli.run_export")
    @patch("wip_toolkit.cli.WIPClient")
    def test_export_basic(self, MockClient, mock_run_export):
        from wip_toolkit.cli import main

        mock_client = _make_mock_client()
        MockClient.return_value = mock_client
        mock_run_export.return_value = ExportStats(
            namespace="wip", counts=EntityCounts()
        )

        runner = CliRunner()
        result = runner.invoke(main, ["export", "wip", "/tmp/test.zip"])

        assert result.exit_code == 0
        mock_run_export.assert_called_once()

    @patch("wip_toolkit.cli.run_export")
    @patch("wip_toolkit.cli.WIPClient")
    def test_export_with_all_flags(self, MockClient, mock_run_export):
        from wip_toolkit.cli import main

        mock_client = _make_mock_client()
        MockClient.return_value = mock_client
        mock_run_export.return_value = ExportStats(
            namespace="wip", counts=EntityCounts()
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "export", "wip", "/tmp/out.zip",
            "--include-files",
            "--include-inactive",
            "--skip-documents",
            "--skip-closure",
            "--latest-only",
            "--dry-run",
        ])

        assert result.exit_code == 0
        mock_run_export.assert_called_once()

    @patch("wip_toolkit.cli.run_export")
    @patch("wip_toolkit.cli.WIPClient")
    def test_export_unhealthy_service_exits(self, MockClient, mock_run_export):
        from wip_toolkit.cli import main

        mock_client = _make_mock_client(healthy=False)
        MockClient.return_value = mock_client

        runner = CliRunner()
        result = runner.invoke(main, ["export", "wip", "/tmp/test.zip"])

        assert result.exit_code != 0
        mock_run_export.assert_not_called()

    @patch("wip_toolkit.cli.run_export")
    @patch("wip_toolkit.cli.WIPClient")
    def test_export_passes_options(self, MockClient, mock_run_export):
        from wip_toolkit.cli import main

        mock_client = _make_mock_client()
        MockClient.return_value = mock_client
        mock_run_export.return_value = ExportStats(
            namespace="wip", counts=EntityCounts()
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "export", "wip", "/tmp/out.zip",
            "--include-files",
            "--skip-documents",
            "--latest-only",
            "--dry-run",
        ])

        assert result.exit_code == 0
        _, kwargs = mock_run_export.call_args
        assert kwargs["include_files"] is True
        assert kwargs["skip_documents"] is True
        assert kwargs["latest_only"] is True
        assert kwargs["dry_run"] is True


class TestImportCommand:
    """Tests for the import CLI command."""

    @patch("wip_toolkit.cli.run_import")
    @patch("wip_toolkit.cli.WIPClient")
    def test_import_restore_mode(self, MockClient, mock_run_import):
        from wip_toolkit.cli import main

        mock_client = _make_mock_client()
        MockClient.return_value = mock_client
        mock_run_import.return_value = ImportStats(
            mode="restore", target_namespace="wip"
        )

        runner = CliRunner()
        result = runner.invoke(main, ["import", "/tmp/test.zip"])

        assert result.exit_code == 0
        _, kwargs = mock_run_import.call_args
        assert kwargs["mode"] == "restore"

    @patch("wip_toolkit.cli.run_import")
    @patch("wip_toolkit.cli.WIPClient")
    def test_import_fresh_mode(self, MockClient, mock_run_import):
        from wip_toolkit.cli import main

        mock_client = _make_mock_client()
        MockClient.return_value = mock_client
        mock_run_import.return_value = ImportStats(
            mode="fresh", target_namespace="wip"
        )

        runner = CliRunner()
        result = runner.invoke(main, ["import", "/tmp/test.zip", "--mode", "fresh"])

        assert result.exit_code == 0
        _, kwargs = mock_run_import.call_args
        assert kwargs["mode"] == "fresh"

    @patch("wip_toolkit.cli.run_import")
    @patch("wip_toolkit.cli.WIPClient")
    def test_import_with_options(self, MockClient, mock_run_import):
        from wip_toolkit.cli import main

        mock_client = _make_mock_client()
        MockClient.return_value = mock_client
        mock_run_import.return_value = ImportStats(
            mode="fresh", target_namespace="new-ns"
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "import", "/tmp/test.zip",
            "--mode", "fresh",
            "--target-namespace", "new-ns",
            "--register-synonyms",
            "--batch-size", "200",
            "--continue-on-error",
            "--dry-run",
        ])

        assert result.exit_code == 0
        _, kwargs = mock_run_import.call_args
        assert kwargs["target_namespace"] == "new-ns"
        assert kwargs["register_synonyms"] is True
        assert kwargs["batch_size"] == 200
        assert kwargs["continue_on_error"] is True
        assert kwargs["dry_run"] is True

    @patch("wip_toolkit.cli.run_import")
    @patch("wip_toolkit.cli.WIPClient")
    def test_import_errors_exit_1(self, MockClient, mock_run_import):
        from wip_toolkit.cli import main

        mock_client = _make_mock_client()
        MockClient.return_value = mock_client

        stats = ImportStats(mode="restore", target_namespace="wip")
        stats.errors = ["Something went wrong"]
        mock_run_import.return_value = stats

        runner = CliRunner()
        result = runner.invoke(main, ["import", "/tmp/test.zip"])

        assert result.exit_code == 1

    @patch("wip_toolkit.cli.run_import")
    @patch("wip_toolkit.cli.WIPClient")
    def test_import_success_exit_0(self, MockClient, mock_run_import):
        from wip_toolkit.cli import main

        mock_client = _make_mock_client()
        MockClient.return_value = mock_client
        mock_run_import.return_value = ImportStats(
            mode="restore", target_namespace="wip"
        )

        runner = CliRunner()
        result = runner.invoke(main, ["import", "/tmp/test.zip"])

        assert result.exit_code == 0


class TestInspectCommand:
    """Tests for the inspect CLI command."""

    @patch("wip_toolkit.cli.ArchiveReader")
    def test_inspect_basic(self, MockReader):
        from wip_toolkit.cli import main

        manifest = _make_manifest()
        mock_reader = _make_mock_archive_reader(manifest)
        MockReader.return_value = mock_reader

        runner = CliRunner()
        result = runner.invoke(main, ["inspect", "/tmp/test.zip"])

        assert result.exit_code == 0
        assert "Archive Summary" in result.output

    @patch("wip_toolkit.cli.ArchiveReader")
    def test_inspect_file_not_found(self, MockReader):
        from wip_toolkit.cli import main

        MockReader.side_effect = FileNotFoundError("Archive not found")

        runner = CliRunner()
        result = runner.invoke(main, ["inspect", "/tmp/nonexistent.zip"])

        assert result.exit_code == 1

    @patch("wip_toolkit.cli.ArchiveReader")
    def test_inspect_with_show_ids(self, MockReader):
        from wip_toolkit.cli import main

        manifest = _make_manifest()
        mock_reader = _make_mock_archive_reader(manifest)
        mock_reader.read_entities.return_value = iter([])
        MockReader.return_value = mock_reader

        runner = CliRunner()
        result = runner.invoke(main, ["inspect", "/tmp/test.zip", "--show-ids"])

        assert result.exit_code == 0

    @patch("wip_toolkit.cli.ArchiveReader")
    def test_inspect_with_show_references(self, MockReader):
        from wip_toolkit.cli import main

        manifest = _make_manifest()
        mock_reader = _make_mock_archive_reader(manifest)
        mock_reader.read_entities.return_value = iter([])
        MockReader.return_value = mock_reader

        runner = CliRunner()
        result = runner.invoke(main, ["inspect", "/tmp/test.zip", "--show-references"])

        assert result.exit_code == 0
