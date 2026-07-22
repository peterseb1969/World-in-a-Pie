"""Tests for the Click CLI commands in wip_toolkit.cli."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from wip_archive.models import EntityCounts, ExportStats


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
    manifest.namespace_prefixes.return_value = ["wip"]
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
    reader.list_namespaces.return_value = ["wip"]
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


class TestInspectMultiNamespace:
    """inspect against REAL archives (no mocked reader) — CASE-545.

    The reader's omitted-namespace convenience raises on a multi-namespace
    v3 archive, so before the fix even a bare `inspect` died in the entity-
    count loop, and the summary's Namespace row printed the manifest's legacy
    scalar, which the multi-namespace producer sets to "".
    """

    @staticmethod
    def _write_archive(path, ns_entities):
        """A real v3 archive. ns_entities: {ns: {entity_type: [entities]}}."""
        from wip_archive.archive import ArchiveWriter
        from wip_archive.models import EntityCounts, Manifest, NamespaceEntry

        writer = ArchiveWriter(path)
        entries = []
        for ns, by_type in ns_entities.items():
            counts = {}
            for entity_type, entities in by_type.items():
                for e in entities:
                    writer.add_entity(entity_type, e, namespace=ns)
                counts[entity_type] = len(entities)
            entries.append(NamespaceEntry(prefix=ns, counts=EntityCounts(**counts)))

        aggregate = {}
        for entry in entries:
            for field in EntityCounts.model_fields:
                aggregate[field] = aggregate.get(field, 0) + getattr(entry.counts, field)

        manifest = Manifest(
            namespaces=entries,
            namespace=entries[0].prefix if len(entries) == 1 else "",
            counts=EntityCounts(**aggregate),
        )
        writer.write(manifest)
        return path

    def _two_ns_archive(self, tmp_path):
        return self._write_archive(
            tmp_path / "multi.zip",
            {
                "alpha": {
                    "terminologies": [{"terminology_id": "LOV-1", "value": "COLOR"}],
                    "templates": [
                        {"template_id": "TPL-1", "value": "ALPHA_DOC", "version": 1, "fields": []}
                    ],
                },
                "beta": {
                    "terms": [
                        {"term_id": "ITEM-1", "value": "red"},
                        {"term_id": "ITEM-2", "value": "blue"},
                    ],
                },
            },
        )

    def test_inspect_multi_namespace_archive(self, tmp_path):
        from wip_toolkit.cli import main

        archive = self._two_ns_archive(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(archive)])

        assert result.exit_code == 0, result.output
        # Summary lists both namespaces (not the blank legacy scalar).
        assert "alpha, beta" in result.output
        # Per-namespace counts verified against NamespaceEntry.counts —
        # every row matches, so no red mismatch markers.
        assert "OK" in result.output
        assert "[red]" not in result.output

    def test_inspect_multi_namespace_show_ids_and_references(self, tmp_path):
        from wip_toolkit.cli import main

        archive = self._two_ns_archive(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["inspect", str(archive), "--show-ids", "--show-references"]
        )

        assert result.exit_code == 0, result.output
        # IDs from BOTH namespaces are listed, tables labeled per namespace.
        assert "LOV-1" in result.output
        assert "ITEM-1" in result.output
        assert "ITEM-2" in result.output
        # Reference graph reached the alpha template and tags its namespace.
        assert "alpha :: TPL-1" in result.output

    def test_inspect_single_namespace_archive_unchanged(self, tmp_path):
        from wip_toolkit.cli import main

        archive = self._write_archive(
            tmp_path / "single.zip",
            {"wip": {"terminologies": [{"terminology_id": "LOV-9", "value": "STATUS"}]}},
        )
        runner = CliRunner()
        result = runner.invoke(main, ["inspect", str(archive), "--show-ids"])

        assert result.exit_code == 0, result.output
        assert "wip" in result.output
        assert "LOV-9" in result.output
        # No per-namespace table labels in the single-namespace layout.
        assert "— wip" not in result.output


class TestUpdateDocumentCommand:
    """Tests for the update-document CLI command."""

    @patch("wip_toolkit.cli.WIPClient")
    def test_update_document_success(self, MockClient):
        from wip_toolkit.cli import main

        mock_client = _make_mock_client()
        mock_client.patch.return_value = {
            "results": [{
                "index": 0,
                "status": "updated",
                "document_id": "DOC-123",
                "version": 4,
                "is_new": False,
            }],
            "total": 1, "succeeded": 1, "failed": 0,
        }
        MockClient.return_value = mock_client

        runner = CliRunner()
        result = runner.invoke(
            main, ["update-document", "DOC-123", "--patch", '{"score": 92}'],
        )

        assert result.exit_code == 0, result.output
        mock_client.patch.assert_called_once()
        args, kwargs = mock_client.patch.call_args
        assert args[0] == "document-store"
        assert args[1] == "/api/document-store/documents"
        body = kwargs["json"]
        assert body == [{"document_id": "DOC-123", "patch": {"score": 92}}]

    @patch("wip_toolkit.cli.WIPClient")
    def test_update_document_forwards_if_match(self, MockClient):
        from wip_toolkit.cli import main

        mock_client = _make_mock_client()
        mock_client.patch.return_value = {
            "results": [{"index": 0, "status": "updated", "document_id": "DOC-1", "version": 5}],
            "total": 1, "succeeded": 1, "failed": 0,
        }
        MockClient.return_value = mock_client

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["update-document", "DOC-1", "--patch", '{"x": 1}', "--if-match", "4"],
        )

        assert result.exit_code == 0, result.output
        body = mock_client.patch.call_args.kwargs["json"]
        assert body == [{"document_id": "DOC-1", "patch": {"x": 1}, "if_match": 4}]

    @patch("wip_toolkit.cli.WIPClient")
    def test_update_document_forwards_metadata_patch(self, MockClient):
        from wip_toolkit.cli import main

        mock_client = _make_mock_client()
        mock_client.patch.return_value = {
            "results": [{"index": 0, "status": "updated", "document_id": "DOC-1", "version": 2}],
            "total": 1, "succeeded": 1, "failed": 0,
        }
        MockClient.return_value = mock_client

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["update-document", "DOC-1", "--patch", "{}",
             "--metadata-patch", '{"reviewed": true}'],
        )

        assert result.exit_code == 0, result.output
        body = mock_client.patch.call_args.kwargs["json"]
        assert body == [{
            "document_id": "DOC-1",
            "patch": {},
            "metadata_patch": {"reviewed": True},
        }]

    @patch("wip_toolkit.cli.WIPClient")
    def test_update_document_rejects_invalid_metadata_patch_json(self, MockClient):
        from wip_toolkit.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["update-document", "DOC-1", "--patch", "{}",
             "--metadata-patch", "not-json"],
        )
        assert result.exit_code == 2

    @patch("wip_toolkit.cli.WIPClient")
    def test_update_document_error_exits_nonzero(self, MockClient):
        from wip_toolkit.cli import main

        mock_client = _make_mock_client()
        mock_client.patch.return_value = {
            "results": [{
                "index": 0,
                "status": "error",
                "document_id": "DOC-1",
                "error": "Cannot patch identity field",
                "error_code": "identity_field_change",
            }],
            "total": 1, "succeeded": 0, "failed": 1,
        }
        MockClient.return_value = mock_client

        runner = CliRunner()
        result = runner.invoke(
            main, ["update-document", "DOC-1", "--patch", '{"national_id": "X"}'],
        )

        assert result.exit_code == 1
        assert "identity_field_change" in result.output

    def test_update_document_invalid_json_patch(self):
        from wip_toolkit.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main, ["update-document", "DOC-1", "--patch", "{not json}"],
        )

        assert result.exit_code == 2
        assert "Invalid JSON" in result.output

    def test_update_document_non_object_patch_rejected(self):
        from wip_toolkit.cli import main

        runner = CliRunner()
        result = runner.invoke(
            main, ["update-document", "DOC-1", "--patch", "[1, 2, 3]"],
        )

        assert result.exit_code == 2
        assert "must be a JSON object" in result.output
