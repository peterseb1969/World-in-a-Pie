"""Tests for the export orchestrator (run_export and helpers)."""

from unittest.mock import MagicMock, patch

import pytest

from wip_toolkit.models import ClosureInfo, EntityCounts, ExportStats


# ---------------------------------------------------------------------------
# Module-level patch targets
# ---------------------------------------------------------------------------
EXPORTER = "wip_toolkit.export.exporter"


@pytest.fixture
def mock_client():
    """Create a mock WIPClient."""
    client = MagicMock()
    client.config = MagicMock()
    client.config.host = "http://localhost"
    return client


@pytest.fixture
def mock_collector():
    """Create a mock EntityCollector instance with sensible defaults."""
    collector = MagicMock()
    collector.fetch_namespace_config.return_value = {
        "prefix": "wip",
        "description": "Test namespace",
        "isolation_mode": "open",
    }
    collector.fetch_terminologies.return_value = [
        {"terminology_id": "TERM-001", "namespace": "wip", "value": "COUNTRY"},
    ]
    collector.fetch_all_terms.return_value = [
        {"term_id": "T-001", "terminology_id": "TERM-001", "namespace": "wip", "value": "UK"},
    ]
    collector.fetch_templates.return_value = [
        {"template_id": "TPL-001", "namespace": "wip", "version": 1, "fields": []},
    ]
    collector.fetch_template_raw.return_value = {
        "template_id": "TPL-001", "namespace": "wip", "version": 1, "fields": [],
    }
    collector.fetch_documents.return_value = [
        {"document_id": "DOC-001", "namespace": "wip", "version": 1,
         "template_id": "TPL-001", "data": {}},
    ]
    collector.fetch_files.return_value = [
        {"file_id": "FILE-001", "namespace": "wip", "filename": "test.pdf"},
    ]
    collector.fetch_file_content.return_value = b"binary-data"
    collector.fetch_registry_entries.return_value = {}
    return collector


@pytest.fixture
def mock_writer():
    """Create a mock ArchiveWriter instance."""
    writer = MagicMock()
    writer.write.return_value = "/tmp/test-export.zip"
    return writer


# ===========================================================================
# run_export
# ===========================================================================
class TestRunExportBasic:
    """Test the main export flow with default options."""

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_basic_export_writes_archive(self, MockCollector, mock_closure,
                                         MockWriter, mock_client,
                                         mock_collector, mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        stats = run_export(mock_client, "wip", "/tmp/export.zip")

        assert isinstance(stats, ExportStats)
        assert stats.namespace == "wip"
        mock_writer.write.assert_called_once()

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_entities_written_to_archive(self, MockCollector, mock_closure,
                                          MockWriter, mock_client,
                                          mock_collector, mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip")

        # Each entity type should have add_entity called
        entity_types = [c.args[0] for c in mock_writer.add_entity.call_args_list]
        assert "terminologies" in entity_types
        assert "terms" in entity_types
        assert "templates" in entity_types
        assert "documents" in entity_types
        assert "files" in entity_types

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_entities_tagged_with_source_and_namespace(self, MockCollector,
                                                        mock_closure,
                                                        MockWriter,
                                                        mock_client,
                                                        mock_collector,
                                                        mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip")

        # Check that terminologies were tagged before writing
        terminology = mock_collector.fetch_terminologies.return_value[0]
        assert terminology["_source"] == "primary"
        assert terminology["_namespace"] == "wip"

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_collector_created_with_correct_args(self, MockCollector,
                                                  mock_closure, MockWriter,
                                                  mock_client, mock_collector,
                                                  mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip", include_inactive=True)

        MockCollector.assert_called_once_with(mock_client, "wip", include_inactive=True)

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_counts_reflect_entity_counts(self, MockCollector, mock_closure,
                                           MockWriter, mock_client,
                                           mock_collector, mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        stats = run_export(mock_client, "wip", "/tmp/export.zip")

        assert stats.counts.terminologies == 1
        assert stats.counts.terms == 1
        assert stats.counts.templates == 1
        assert stats.counts.documents == 1
        assert stats.counts.files == 1


class TestRunExportDryRun:
    """Test dry-run mode."""

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_dry_run_returns_stats_without_writing(self, MockCollector,
                                                    mock_closure, MockWriter,
                                                    mock_client,
                                                    mock_collector,
                                                    mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        stats = run_export(mock_client, "wip", "/tmp/export.zip", dry_run=True)

        assert isinstance(stats, ExportStats)
        # ArchiveWriter should NOT be instantiated in dry-run mode
        MockWriter.assert_not_called()

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_dry_run_still_fetches_entities(self, MockCollector, mock_closure,
                                             MockWriter, mock_client,
                                             mock_collector, mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip", dry_run=True)

        mock_collector.fetch_terminologies.assert_called_once()
        mock_collector.fetch_templates.assert_called_once()

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_dry_run_counts_match_fetched(self, MockCollector, mock_closure,
                                           MockWriter, mock_client,
                                           mock_collector, mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        stats = run_export(mock_client, "wip", "/tmp/export.zip", dry_run=True)

        assert stats.counts.terminologies == 1
        assert stats.counts.templates == 1
        assert stats.counts.documents == 1


class TestRunExportSkipDocuments:
    """Test skip_documents flag."""

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_skip_documents_no_docs_fetched(self, MockCollector, mock_closure,
                                             MockWriter, mock_client,
                                             mock_collector, mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        stats = run_export(mock_client, "wip", "/tmp/export.zip",
                           skip_documents=True)

        mock_collector.fetch_documents.assert_not_called()
        mock_collector.fetch_files.assert_not_called()
        assert stats.counts.documents == 0
        assert stats.counts.files == 0

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_skip_documents_still_fetches_terms_and_templates(
        self, MockCollector, mock_closure, MockWriter,
        mock_client, mock_collector, mock_writer,
    ):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip", skip_documents=True)

        mock_collector.fetch_terminologies.assert_called_once()
        mock_collector.fetch_all_terms.assert_called_once()
        mock_collector.fetch_templates.assert_called_once()


class TestRunExportSkipClosure:
    """Test skip_closure flag."""

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_skip_closure_not_called(self, MockCollector, mock_closure,
                                      MockWriter, mock_client,
                                      mock_collector, mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip", skip_closure=True)

        mock_closure.assert_not_called()

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_closure_called_by_default(self, MockCollector, mock_closure,
                                        MockWriter, mock_client,
                                        mock_collector, mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip")

        mock_closure.assert_called_once()

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_closure_results_appended(self, MockCollector, mock_closure,
                                       MockWriter, mock_client,
                                       mock_collector, mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer

        extra_term = {"terminology_id": "TERM-EXT", "_source": "closure"}
        extra_term_item = {"term_id": "T-EXT", "_source": "closure"}
        extra_tpl = {"template_id": "TPL-EXT", "_source": "closure"}
        mock_closure.return_value = (
            [extra_term], [extra_term_item], [extra_tpl], ["warning1"],
        )

        from wip_toolkit.export.exporter import run_export
        stats = run_export(mock_client, "wip", "/tmp/export.zip")

        # Terminologies should include the extra from closure
        assert stats.counts.terminologies == 2  # 1 primary + 1 closure
        assert stats.counts.templates == 2  # 1 primary + 1 closure


class TestRunExportIncludeFiles:
    """Test include_files flag for blob downloads."""

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_include_files_downloads_blobs(self, MockCollector, mock_closure,
                                            MockWriter, mock_client,
                                            mock_collector, mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip", include_files=True)

        mock_collector.fetch_file_content.assert_called_once_with("FILE-001")
        mock_writer.add_blob.assert_called_once_with("FILE-001", b"binary-data")

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_no_include_files_skips_blob_download(self, MockCollector,
                                                    mock_closure, MockWriter,
                                                    mock_client,
                                                    mock_collector,
                                                    mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip", include_files=False)

        mock_collector.fetch_file_content.assert_not_called()
        mock_writer.add_blob.assert_not_called()

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_include_files_blob_download_error_continues(self, MockCollector,
                                                          mock_closure,
                                                          MockWriter,
                                                          mock_client,
                                                          mock_collector,
                                                          mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])
        mock_collector.fetch_file_content.side_effect = Exception("Download failed")

        from wip_toolkit.export.exporter import run_export
        # Should not raise
        stats = run_export(mock_client, "wip", "/tmp/export.zip", include_files=True)

        assert isinstance(stats, ExportStats)
        mock_writer.add_blob.assert_not_called()


class TestRunExportLatestOnly:
    """Test latest_only flag."""

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_latest_only_passes_flag_to_collector(self, MockCollector,
                                                    mock_closure, MockWriter,
                                                    mock_client,
                                                    mock_collector,
                                                    mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip", latest_only=True)

        mock_collector.fetch_documents.assert_called_once_with(latest_only=True)

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_default_fetches_all_versions(self, MockCollector, mock_closure,
                                           MockWriter, mock_client,
                                           mock_collector, mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip")

        mock_collector.fetch_documents.assert_called_once_with(latest_only=False)


# ===========================================================================
# _fetch_raw_templates
# ===========================================================================
class TestFetchRawTemplates:
    """Test raw template fetching with fallback."""

    def test_replaces_resolved_with_raw(self):
        from wip_toolkit.export.exporter import _fetch_raw_templates

        collector = MagicMock()
        resolved = {"template_id": "TPL-001", "version": 1, "fields": [{"resolved": True}]}
        raw = {"template_id": "TPL-001", "version": 1, "fields": [{"raw": True}]}
        collector.fetch_template_raw.return_value = raw

        result = _fetch_raw_templates(collector, [resolved])

        assert len(result) == 1
        assert result[0]["fields"] == [{"raw": True}]
        collector.fetch_template_raw.assert_called_once_with("TPL-001", 1)

    def test_falls_back_to_resolved_on_error(self):
        from wip_toolkit.export.exporter import _fetch_raw_templates

        collector = MagicMock()
        resolved = {"template_id": "TPL-001", "version": 1, "fields": [{"resolved": True}]}
        collector.fetch_template_raw.side_effect = Exception("Not found")

        result = _fetch_raw_templates(collector, [resolved])

        assert len(result) == 1
        assert result[0]["fields"] == [{"resolved": True}]

    def test_uses_default_version_1(self):
        from wip_toolkit.export.exporter import _fetch_raw_templates

        collector = MagicMock()
        resolved = {"template_id": "TPL-001", "fields": []}  # no version key
        collector.fetch_template_raw.return_value = resolved

        _fetch_raw_templates(collector, [resolved])

        collector.fetch_template_raw.assert_called_once_with("TPL-001", 1)

    def test_multiple_templates(self):
        from wip_toolkit.export.exporter import _fetch_raw_templates

        collector = MagicMock()
        tpl1 = {"template_id": "TPL-001", "version": 1, "fields": []}
        tpl2 = {"template_id": "TPL-002", "version": 2, "fields": []}
        raw1 = {"template_id": "TPL-001", "version": 1, "fields": [], "raw": True}
        raw2 = {"template_id": "TPL-002", "version": 2, "fields": [], "raw": True}
        collector.fetch_template_raw.side_effect = [raw1, raw2]

        result = _fetch_raw_templates(collector, [tpl1, tpl2])

        assert len(result) == 2
        assert result[0]["raw"] is True
        assert result[1]["raw"] is True

    def test_partial_failure(self):
        """First template raw succeeds, second fails and falls back."""
        from wip_toolkit.export.exporter import _fetch_raw_templates

        collector = MagicMock()
        tpl1 = {"template_id": "TPL-001", "version": 1, "fields": []}
        tpl2 = {"template_id": "TPL-002", "version": 1, "fields": [{"resolved": True}]}
        raw1 = {"template_id": "TPL-001", "version": 1, "fields": [], "raw": True}
        collector.fetch_template_raw.side_effect = [raw1, Exception("500")]

        result = _fetch_raw_templates(collector, [tpl1, tpl2])

        assert len(result) == 2
        assert result[0].get("raw") is True
        assert result[1]["fields"] == [{"resolved": True}]

    def test_empty_templates(self):
        from wip_toolkit.export.exporter import _fetch_raw_templates

        collector = MagicMock()
        result = _fetch_raw_templates(collector, [])

        assert result == []
        collector.fetch_template_raw.assert_not_called()


# ===========================================================================
# _enrich_with_registry
# ===========================================================================
class TestEnrichWithRegistry:
    """Test Registry enrichment of entities."""

    def test_injects_registry_into_entities(self):
        from wip_toolkit.export.exporter import _enrich_with_registry

        collector = MagicMock()
        collector.fetch_registry_entries.return_value = {
            "TERM-001": {"entry_id": "TERM-001", "entity_type": "terminologies"},
            "TPL-001": {"entry_id": "TPL-001", "entity_type": "templates"},
        }

        terminologies = [{"terminology_id": "TERM-001"}]
        templates = [{"template_id": "TPL-001"}]

        _enrich_with_registry(collector, terminologies, [], templates, [], [])

        assert terminologies[0]["_registry"]["entry_id"] == "TERM-001"
        assert templates[0]["_registry"]["entry_id"] == "TPL-001"

    def test_deduplicates_ids_for_bulk_lookup(self):
        from wip_toolkit.export.exporter import _enrich_with_registry

        collector = MagicMock()
        collector.fetch_registry_entries.return_value = {}

        # Same template_id in two different version entries
        templates = [
            {"template_id": "TPL-001", "version": 1},
            {"template_id": "TPL-001", "version": 2},
        ]

        _enrich_with_registry(collector, [], [], templates, [], [])

        # Should only see TPL-001 once in the lookup call
        call_ids = collector.fetch_registry_entries.call_args[0][0]
        assert call_ids.count("TPL-001") == 1

    def test_no_entities_skips_lookup(self):
        from wip_toolkit.export.exporter import _enrich_with_registry

        collector = MagicMock()

        _enrich_with_registry(collector, [], [], [], [], [])

        collector.fetch_registry_entries.assert_not_called()

    def test_missing_registry_entry_not_injected(self):
        from wip_toolkit.export.exporter import _enrich_with_registry

        collector = MagicMock()
        collector.fetch_registry_entries.return_value = {}

        terminologies = [{"terminology_id": "TERM-001"}]

        _enrich_with_registry(collector, terminologies, [], [], [], [])

        assert "_registry" not in terminologies[0]

    def test_enriches_all_entity_types(self):
        from wip_toolkit.export.exporter import _enrich_with_registry

        collector = MagicMock()
        reg_data = {"entry_id": "X", "entity_type": "any"}
        collector.fetch_registry_entries.return_value = {
            "TERM-001": reg_data,
            "T-001": reg_data,
            "TPL-001": reg_data,
            "DOC-001": reg_data,
            "FILE-001": reg_data,
        }

        terminologies = [{"terminology_id": "TERM-001"}]
        terms = [{"term_id": "T-001"}]
        templates = [{"template_id": "TPL-001"}]
        documents = [{"document_id": "DOC-001"}]
        files = [{"file_id": "FILE-001"}]

        _enrich_with_registry(collector, terminologies, terms, templates,
                              documents, files)

        assert "_registry" in terminologies[0]
        assert "_registry" in terms[0]
        assert "_registry" in templates[0]
        assert "_registry" in documents[0]
        assert "_registry" in files[0]


# ===========================================================================
# _build_stats
# ===========================================================================
class TestBuildStats:
    """Test ExportStats construction."""

    def test_constructs_stats_with_counts(self):
        from wip_toolkit.export.exporter import _build_stats

        counts = EntityCounts(terminologies=2, terms=10, templates=3,
                              documents=5, files=1)
        closure_info = ClosureInfo(
            external_terminologies=["EXT-TERM-1"],
            external_templates=["EXT-TPL-1", "EXT-TPL-2"],
            iterations=2,
            warnings=["some warning"],
        )

        # Use a fixed start time so we can verify duration is positive
        import time
        start = time.monotonic() - 1.5

        stats = _build_stats("wip", counts, closure_info, start)

        assert stats.namespace == "wip"
        assert stats.counts.terminologies == 2
        assert stats.counts.terms == 10
        assert stats.closure_iterations == 2
        assert stats.external_terminologies == 1
        assert stats.external_templates == 2
        assert stats.warnings == ["some warning"]
        assert stats.duration_seconds > 0

    def test_empty_closure_info(self):
        from wip_toolkit.export.exporter import _build_stats
        import time

        counts = EntityCounts()
        closure_info = ClosureInfo()

        stats = _build_stats("test", counts, closure_info, time.monotonic())

        assert stats.namespace == "test"
        assert stats.closure_iterations == 0
        assert stats.external_terminologies == 0
        assert stats.external_templates == 0
        assert stats.warnings == []
