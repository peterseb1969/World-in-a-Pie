"""Tests for the streaming export orchestrator (run_export and helpers)."""

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
        {"terminology_id": "0190a000-0000-7000-0000-000000000001", "namespace": "wip", "value": "COUNTRY"},
    ]
    collector.fetch_all_terms.return_value = [
        {"term_id": "0190b000-0000-7000-0000-000000000001", "terminology_id": "0190a000-0000-7000-0000-000000000001", "namespace": "wip", "value": "UK"},
    ]
    collector.fetch_templates.return_value = [
        {"template_id": "0190c000-0000-7000-0000-000000000001", "namespace": "wip", "version": 1, "fields": []},
    ]
    collector.fetch_template_raw.return_value = {
        "template_id": "0190c000-0000-7000-0000-000000000001", "namespace": "wip", "version": 1, "fields": [],
    }
    # stream_documents yields pages
    collector.stream_documents.return_value = iter([
        [{"document_id": "0190d000-0000-7000-0000-000000000001", "namespace": "wip", "version": 1,
          "template_id": "0190c000-0000-7000-0000-000000000001", "data": {}}],
    ])
    # fetch_documents for closure
    collector.fetch_documents.return_value = [
        {"document_id": "0190d000-0000-7000-0000-000000000001", "namespace": "wip", "version": 1,
         "template_id": "0190c000-0000-7000-0000-000000000001", "data": {}},
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
    writer.entity_count.return_value = 1
    writer._tmp_dir = "/tmp/mock-tmp-dir"
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
    def test_uses_stream_documents(self, MockCollector, mock_closure,
                                     MockWriter, mock_client,
                                     mock_collector, mock_writer):
        """Exporter uses stream_documents for O(page_size) memory."""
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip")

        mock_collector.stream_documents.assert_called_once()

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_no_registry_enrichment_in_default_export(self, MockCollector,
                                                        mock_closure,
                                                        MockWriter,
                                                        mock_client,
                                                        mock_collector,
                                                        mock_writer):
        """Default export should NOT inject _registry metadata into entities."""
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip")

        # Entities should not have _registry injected
        terminology = mock_collector.fetch_terminologies.return_value[0]
        assert "_registry" not in terminology


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

        mock_collector.stream_documents.assert_not_called()
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


class TestRunExportSkipSynonyms:
    """Test skip_synonyms flag."""

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_skip_synonyms_no_registry_lookup(self, MockCollector, mock_closure,
                                                MockWriter, mock_client,
                                                mock_collector, mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip", skip_synonyms=True)

        # No Registry lookups for synonyms
        mock_collector.fetch_registry_entries.assert_not_called()
        mock_writer.write_synonyms_file.assert_not_called()


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


class TestRunExportLatestOnly:
    """Test latest_only flag."""

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_latest_only_passes_flag_to_stream(self, MockCollector,
                                                 mock_closure, MockWriter,
                                                 mock_client,
                                                 mock_collector,
                                                 mock_writer):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        run_export(mock_client, "wip", "/tmp/export.zip", latest_only=True)

        mock_collector.stream_documents.assert_called_once_with(
            latest_only=True, page_size=1000
        )


# ===========================================================================
# _fetch_raw_templates
# ===========================================================================
class TestFetchRawTemplates:
    """Test raw template fetching with fallback."""

    def test_replaces_resolved_with_raw(self):
        from wip_toolkit.export.exporter import _fetch_raw_templates

        collector = MagicMock()
        resolved = {"template_id": "0190c000-0000-7000-0000-000000000001", "version": 1, "fields": [{"resolved": True}]}
        raw = {"template_id": "0190c000-0000-7000-0000-000000000001", "version": 1, "fields": [{"raw": True}]}
        collector.fetch_template_raw.return_value = raw

        result = _fetch_raw_templates(collector, [resolved])

        assert len(result) == 1
        assert result[0]["fields"] == [{"raw": True}]

    def test_falls_back_to_resolved_on_error(self):
        from wip_toolkit.export.exporter import _fetch_raw_templates

        collector = MagicMock()
        resolved = {"template_id": "0190c000-0000-7000-0000-000000000001", "version": 1, "fields": [{"resolved": True}]}
        collector.fetch_template_raw.side_effect = Exception("Not found")

        result = _fetch_raw_templates(collector, [resolved])

        assert len(result) == 1
        assert result[0]["fields"] == [{"resolved": True}]

    def test_empty_templates(self):
        from wip_toolkit.export.exporter import _fetch_raw_templates

        collector = MagicMock()
        result = _fetch_raw_templates(collector, [])

        assert result == []
        collector.fetch_template_raw.assert_not_called()


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
            external_terminologies=["EXT-0190a000-0000-7000-0000-000000000001"],
            external_templates=["EXT-0190c000-0000-7000-0000-000000000001", "EXT-TPL-2"],
            iterations=2,
            warnings=["some warning"],
        )

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
        import time

        from wip_toolkit.export.exporter import _build_stats

        counts = EntityCounts()
        closure_info = ClosureInfo()

        stats = _build_stats("test", counts, closure_info, time.monotonic())

        assert stats.namespace == "test"
        assert stats.closure_iterations == 0
        assert stats.external_terminologies == 0


# ===========================================================================
# progress_callback + non_interactive (CASE-23 backup/restore prep)
# ===========================================================================
class TestRunExportProgressCallback:
    """Verify progress_callback observes phase boundaries and is fault-tolerant."""

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_callback_receives_start_and_complete(
        self, MockCollector, mock_closure, MockWriter,
        mock_client, mock_collector, mock_writer,
    ):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        events = []

        from wip_toolkit.export.exporter import run_export
        run_export(
            mock_client, "wip", "/tmp/export.zip",
            progress_callback=events.append,
        )

        phases = [e.phase for e in events]
        assert phases[0] == "start"
        assert phases[-1] == "complete"
        assert events[0].percent == 0.0
        assert events[-1].percent == 100.0
        assert events[-1].details["counts"]["terminologies"] == 1

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_callback_observes_all_default_phases(
        self, MockCollector, mock_closure, MockWriter,
        mock_client, mock_collector, mock_writer,
    ):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        events = []

        from wip_toolkit.export.exporter import run_export
        run_export(
            mock_client, "wip", "/tmp/export.zip",
            progress_callback=events.append,
        )

        phases = {e.phase for e in events}
        # All five default phases plus start + complete
        assert "start" in phases
        assert "phase_1a_entities" in phases
        assert "phase_closure" in phases
        assert "phase_1b_documents" in phases
        assert "phase_2_synonyms" in phases
        assert "phase_3_finalize" in phases
        assert "complete" in phases

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_callback_emits_file_phase_when_include_files(
        self, MockCollector, mock_closure, MockWriter,
        mock_client, mock_collector, mock_writer,
    ):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        events = []

        from wip_toolkit.export.exporter import run_export
        run_export(
            mock_client, "wip", "/tmp/export.zip",
            include_files=True,
            progress_callback=events.append,
        )

        phases = [e.phase for e in events]
        assert "phase_1c_files" in phases

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_callback_emits_warning_when_files_skipped(
        self, MockCollector, mock_closure, MockWriter,
        mock_client, mock_collector, mock_writer,
    ):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        events = []

        from wip_toolkit.export.exporter import run_export
        run_export(
            mock_client, "wip", "/tmp/export.zip",
            include_files=False,  # mock_collector returns one file
            non_interactive=True,
            progress_callback=events.append,
        )

        warning_events = [e for e in events if e.phase == "warning_files_skipped"]
        assert len(warning_events) == 1
        assert warning_events[0].details["file_count"] == 1

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_callback_exception_does_not_break_export(
        self, MockCollector, mock_closure, MockWriter,
        mock_client, mock_collector, mock_writer,
    ):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        def explosive(_event):
            raise RuntimeError("observer is broken")

        from wip_toolkit.export.exporter import run_export
        # Must complete normally despite the callback raising on every event
        stats = run_export(
            mock_client, "wip", "/tmp/export.zip",
            progress_callback=explosive,
        )

        assert stats.namespace == "wip"
        mock_writer.write.assert_called_once()

    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_no_callback_is_supported(
        self, MockCollector, mock_closure, MockWriter,
        mock_client, mock_collector, mock_writer,
    ):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])

        from wip_toolkit.export.exporter import run_export
        # Default (None) callback must not raise
        stats = run_export(mock_client, "wip", "/tmp/export.zip")
        assert stats.namespace == "wip"


class TestRunExportNonInteractive:
    """Verify non_interactive mode never prompts and never exits unexpectedly."""

    @patch(f"{EXPORTER}.click.confirm")
    @patch(f"{EXPORTER}.sys.stdin")
    @patch(f"{EXPORTER}.ArchiveWriter")
    @patch(f"{EXPORTER}.compute_closure")
    @patch(f"{EXPORTER}.EntityCollector")
    def test_non_interactive_skips_confirm_even_with_tty(
        self, MockCollector, mock_closure, MockWriter,
        mock_stdin, mock_confirm,
        mock_client, mock_collector, mock_writer,
    ):
        MockCollector.return_value = mock_collector
        MockWriter.return_value = mock_writer
        mock_closure.return_value = ([], [], [], [])
        # Force isatty=True so the interactive branch *would* normally fire
        mock_stdin.isatty.return_value = True
        # If confirm were called and returned False the export would SystemExit
        mock_confirm.return_value = False

        from wip_toolkit.export.exporter import run_export
        stats = run_export(
            mock_client, "wip", "/tmp/export.zip",
            include_files=False,
            non_interactive=True,
        )

        mock_confirm.assert_not_called()
        assert stats.namespace == "wip"
