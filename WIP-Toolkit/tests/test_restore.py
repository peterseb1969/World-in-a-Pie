"""Tests for wip_toolkit.import_.restore — simplified restore mode import.

The simplified flow removes pre-registration phases. Services handle
Registry registration during their create flows when document_id/template_id
is passed through.
"""

from unittest.mock import MagicMock, call, patch

import pytest

from wip_toolkit.client import WIPClientError
from wip_toolkit.import_.restore import (
    _activate_templates,
    _create_documents_streamed,
    _create_templates,
    _create_terms,
    _create_terminologies,
    _ensure_namespace,
    _restore_synonyms,
    _upload_files,
    restore_import,
)
from wip_toolkit.models import EntityCounts, ImportStats


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client():
    """Create a mock WIPClient with standard response defaults."""
    client = MagicMock()
    client.post.return_value = {
        "created": 0,
        "already_exists": 0,
        "errors": 0,
        "results": [],
    }
    return client


def _make_reader(
    *,
    namespace="source-ns",
    terminologies=None,
    terms=None,
    templates=None,
    documents=None,
    files=None,
    blobs=None,
    has_synonyms=False,
    synonyms=None,
):
    """Create a mock ArchiveReader with entity data."""
    reader = MagicMock()

    terminologies = terminologies or []
    terms = terms or []
    templates = templates or []
    documents = documents or []
    files = files or []
    blobs = blobs or []
    synonyms = synonyms or []

    manifest = MagicMock()
    manifest.namespace = namespace
    manifest.counts = EntityCounts(
        terminologies=len(terminologies),
        terms=len(terms),
        templates=len(templates),
        documents=len(documents),
        files=len(files),
    )
    reader.read_manifest.return_value = manifest

    def _read_entities(entity_type):
        mapping = {
            "terminologies": terminologies,
            "terms": terms,
            "templates": templates,
            "documents": documents,
            "files": files,
        }
        return iter(mapping.get(entity_type, []))

    reader.read_entities.side_effect = _read_entities
    reader.list_blobs.return_value = blobs
    reader.read_blob.side_effect = lambda fid: b"fake-blob-content" if fid in blobs else None
    reader.has_synonyms.return_value = has_synonyms
    reader.read_synonyms.return_value = iter(synonyms)
    return reader


def _make_stats(ns="target-ns"):
    return ImportStats(mode="restore", target_namespace=ns)


def _bulk_result(status="created", index=0, entity_id="NEW-ID"):
    return {
        "results": [{"index": index, "status": status, "id": entity_id}],
        "succeeded": 1 if status == "created" else 0,
        "failed": 0 if status != "error" else 1,
    }


def _bulk_error(error_msg="already exists", index=0):
    return {
        "results": [{"index": index, "status": "error", "error": error_msg}],
        "succeeded": 0,
        "failed": 1,
    }


# ---------------------------------------------------------------------------
# Sample data builders
# ---------------------------------------------------------------------------

def _terminology(tid="TERM-000001", value="COUNTRY", label="Country", **overrides):
    base = {
        "terminology_id": tid,
        "value": value,
        "label": label,
        "namespace": "source-ns",
        "status": "active",
    }
    base.update(overrides)
    return base


def _term(tid="T-000001", terminology_id="TERM-000001", value="United Kingdom", **overrides):
    base = {
        "term_id": tid,
        "terminology_id": terminology_id,
        "value": value,
        "namespace": "source-ns",
    }
    base.update(overrides)
    return base


def _template(
    tid="TPL-000001",
    value="PERSON",
    version=1,
    **overrides,
):
    base = {
        "template_id": tid,
        "value": value,
        "label": value.title(),
        "version": version,
        "namespace": "source-ns",
        "extends": None,
        "extends_version": None,
        "identity_fields": [],
        "fields": [{"name": "name", "type": "string"}],
        "rules": [],
    }
    base.update(overrides)
    return base


def _document(
    did="DOC-000001",
    template_id="TPL-000001",
    version=1,
    data=None,
    **overrides,
):
    base = {
        "document_id": did,
        "template_id": template_id,
        "template_version": 1,
        "version": version,
        "namespace": "source-ns",
        "data": data or {"name": "Test"},
    }
    base.update(overrides)
    return base


def _file(fid="FILE-000001", filename="test.txt", **overrides):
    base = {
        "file_id": fid,
        "filename": filename,
        "content_type": "text/plain",
        "namespace": "source-ns",
        "metadata": {},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Full flow tests
# ---------------------------------------------------------------------------

class TestRestoreFullFlow:
    """End-to-end restore_import tests."""

    @patch("wip_toolkit.import_.restore.console")
    def test_restore_full_flow(self, mock_console):
        """Full restore with all entity types — no pre-registration steps."""
        client = _make_client()
        terminologies = [_terminology()]
        terms = [_term()]
        templates = [_template()]
        documents = [_document()]
        files = [_file(fid="FILE-000001")]

        reader = _make_reader(
            terminologies=terminologies,
            terms=terms,
            templates=templates,
            documents=documents,
            files=files,
            blobs=["FILE-000001"],
        )

        def post_router(service, path, **kwargs):
            if path == "/terminologies":
                return _bulk_result(status="created")
            if path.startswith("/terminologies/") and "/terms" in path:
                return {"succeeded": 1, "failed": 0, "results": []}
            if path == "/templates":
                return _bulk_result(status="created")
            if path.endswith("/activate"):
                return {"status": "active"}
            if path == "/documents":
                return {"succeeded": 1, "failed": 0, "results": []}
            if path == "/synonyms/add":
                return {"results": []}
            return {"results": []}

        client.post.side_effect = post_router
        client.put.return_value = _bulk_result(status="created")
        client.post_form.return_value = {"status": "created"}
        client.get.return_value = {"prefix": "target-ns"}

        stats = restore_import(client, reader, "target-ns")

        assert stats.mode == "restore"
        assert stats.target_namespace == "target-ns"
        assert stats.source_namespace == "source-ns"
        # Verify reader was asked for entity types
        reader.read_entities.assert_any_call("terminologies")
        reader.read_entities.assert_any_call("terms")
        reader.read_entities.assert_any_call("templates")
        reader.read_entities.assert_any_call("documents")
        reader.read_entities.assert_any_call("files")

    @patch("wip_toolkit.import_.restore.console")
    def test_no_preregistration_calls(self, mock_console):
        """Simplified flow should NOT call Registry /entries/register."""
        client = _make_client()
        reader = _make_reader(
            terminologies=[_terminology()],
            terms=[_term()],
            templates=[_template()],
            documents=[_document()],
        )

        def post_router(service, path, **kwargs):
            if path == "/terminologies":
                return _bulk_result(status="created")
            if path.startswith("/terminologies/") and "/terms" in path:
                return {"succeeded": 1, "failed": 0, "results": []}
            if path == "/templates":
                return _bulk_result(status="created")
            if path.endswith("/activate"):
                return {"status": "active"}
            if path == "/documents":
                return {"succeeded": 1, "failed": 0, "results": []}
            return {"results": []}

        client.post.side_effect = post_router
        client.get.return_value = {"prefix": "target-ns"}

        restore_import(client, reader, "target-ns")

        # Assert /entries/register was NEVER called
        for c in client.post.call_args_list:
            assert c[0][1] != "/entries/register", \
                "Simplified restore should not call Registry /entries/register"

    @patch("wip_toolkit.import_.restore.console")
    def test_restore_dry_run(self, mock_console):
        """Dry run returns empty stats and makes no API calls."""
        client = _make_client()
        reader = _make_reader(
            terminologies=[_terminology()],
            terms=[_term()],
            templates=[_template()],
        )

        stats = restore_import(client, reader, "target-ns", dry_run=True)

        assert stats.mode == "restore"
        assert stats.source_namespace == "source-ns"
        assert stats.created.total == 0
        client.get.assert_not_called()
        client.post.assert_not_called()
        client.put.assert_not_called()


# ---------------------------------------------------------------------------
# Namespace tests
# ---------------------------------------------------------------------------

class TestEnsureNamespace:

    @patch("wip_toolkit.import_.restore.console")
    def test_ensure_namespace_exists(self, mock_console):
        client = _make_client()
        client.get.return_value = {"prefix": "target-ns", "description": "existing"}
        stats = _make_stats()

        _ensure_namespace(client, "target-ns", stats)

        client.get.assert_called_once_with("registry", "/namespaces/target-ns")
        client.post.assert_not_called()

    @patch("wip_toolkit.import_.restore.console")
    def test_ensure_namespace_created(self, mock_console):
        client = _make_client()
        client.get.side_effect = WIPClientError("Not found", status_code=404)
        client.post.return_value = {"prefix": "target-ns"}
        stats = _make_stats()

        _ensure_namespace(client, "target-ns", stats)

        client.post.assert_called_once_with("registry", "/namespaces", json={
            "prefix": "target-ns",
            "description": "Restored from backup",
            "isolation_mode": "open",
            "created_by": "wip-toolkit",
        })


# ---------------------------------------------------------------------------
# Create entities tests
# ---------------------------------------------------------------------------

class TestCreateTerminologies:

    @patch("wip_toolkit.import_.restore.console")
    def test_create_terminologies_success(self, mock_console):
        client = _make_client()
        client.post.return_value = _bulk_result(status="created")
        stats = _make_stats()

        _create_terminologies(client, "target-ns", [_terminology()], stats, False)

        assert stats.created.terminologies == 1

    @patch("wip_toolkit.import_.restore.console")
    def test_create_terminologies_already_exists(self, mock_console):
        client = _make_client()
        client.post.return_value = _bulk_error("already exists")
        stats = _make_stats()

        _create_terminologies(client, "target-ns", [_terminology()], stats, True)

        assert stats.skipped.terminologies == 1


class TestCreateTerms:

    @patch("wip_toolkit.import_.restore.console")
    def test_create_terms_batched(self, mock_console):
        client = _make_client()
        client.post.return_value = {"succeeded": 2, "failed": 0, "results": []}
        stats = _make_stats()

        terms = [_term(f"T-{i:03d}", "TERM-001", f"value_{i}") for i in range(5)]
        _create_terms(client, "target-ns", terms, 2, stats, False)

        assert client.post.call_count == 3

    @patch("wip_toolkit.import_.restore.console")
    def test_create_terms_grouped_by_terminology(self, mock_console):
        client = _make_client()
        client.post.return_value = {"succeeded": 1, "failed": 0, "results": []}
        stats = _make_stats()

        terms = [
            _term("T-001", "TERM-001", "UK"),
            _term("T-002", "TERM-002", "active"),
        ]
        _create_terms(client, "target-ns", terms, 50, stats, False)

        call_paths = [c[0][1] for c in client.post.call_args_list]
        assert "/terminologies/TERM-001/terms" in call_paths
        assert "/terminologies/TERM-002/terms" in call_paths


class TestCreateTemplates:

    @patch("wip_toolkit.import_.restore.console")
    def test_create_templates_version_ordered(self, mock_console):
        client = _make_client()
        client.post.return_value = _bulk_result(status="created")
        client.put.return_value = _bulk_result(status="created")
        stats = _make_stats()

        templates = [
            _template("TPL-001", "PERSON", version=2),
            _template("TPL-001", "PERSON", version=1),
        ]
        _create_templates(client, "target-ns", templates, stats, False)

        # v1 should be POST, v2 should be PUT
        assert client.post.call_count == 1
        assert client.put.call_count == 1

    @patch("wip_toolkit.import_.restore.console")
    def test_create_templates_success(self, mock_console):
        client = _make_client()
        client.post.return_value = _bulk_result(status="created")
        stats = _make_stats()

        _create_templates(client, "target-ns", [_template()], stats, False)

        assert stats.created.templates == 1


class TestCreateDocumentsStreamed:

    @patch("wip_toolkit.import_.restore.console")
    def test_create_documents_batched(self, mock_console):
        """Documents respect batch_size."""
        client = _make_client()
        client.post.return_value = {"succeeded": 2, "failed": 0, "results": []}
        stats = _make_stats()

        documents = [_document(f"DOC-{i:03d}", version=1) for i in range(5)]
        reader = _make_reader(documents=documents)

        _create_documents_streamed(client, "target-ns", reader, 2, stats, False)

        # 5 docs / batch_size 2 = 3 batches
        assert client.post.call_count == 3

    @patch("wip_toolkit.import_.restore.console")
    def test_create_documents_version_ordered(self, mock_console):
        """Documents grouped by document_id are sorted by version."""
        client = _make_client()
        call_payloads = []

        def capture_post(service, path, **kwargs):
            if path == "/documents":
                call_payloads.append(kwargs["json"])
            return {"succeeded": len(kwargs.get("json", [])), "failed": 0, "results": []}

        client.post.side_effect = capture_post
        stats = _make_stats()

        documents = [
            _document("DOC-001", version=3),
            _document("DOC-001", version=1),
            _document("DOC-001", version=2),
        ]
        reader = _make_reader(documents=documents)

        _create_documents_streamed(client, "target-ns", reader, 50, stats, False)

        assert len(call_payloads) == 1
        versions = [d["version"] for d in call_payloads[0]]
        assert versions == [1, 2, 3]

    @patch("wip_toolkit.import_.restore.console")
    def test_create_documents_payload_structure(self, mock_console):
        client = _make_client()
        client.post.return_value = {"succeeded": 1, "failed": 0, "results": []}
        stats = _make_stats()

        doc = _document("DOC-001", "TPL-001", version=1, data={"name": "Alice"})
        reader = _make_reader(documents=[doc])

        _create_documents_streamed(client, "target-ns", reader, 50, stats, False)

        args, kwargs = client.post.call_args
        assert args == ("document-store", "/documents")
        items = kwargs["json"]
        assert items[0]["document_id"] == "DOC-001"
        assert items[0]["namespace"] == "target-ns"
        assert items[0]["created_by"] == "wip-toolkit-restore"


# ---------------------------------------------------------------------------
# Activate templates tests
# ---------------------------------------------------------------------------

class TestActivateTemplates:

    @patch("wip_toolkit.import_.restore.console")
    def test_activate_templates(self, mock_console):
        client = _make_client()
        client.post.return_value = {"status": "active"}
        stats = _make_stats()

        templates = [
            _template("TPL-001", "PERSON", version=1),
            _template("TPL-002", "PROJECT", version=1),
        ]
        _activate_templates(client, "target-ns", templates, stats, False)

        assert client.post.call_count == 2

    @patch("wip_toolkit.import_.restore.console")
    def test_activate_deduplicates_versions(self, mock_console):
        client = _make_client()
        client.post.return_value = {"status": "active"}
        stats = _make_stats()

        templates = [
            _template("TPL-001", "PERSON", version=1),
            _template("TPL-001", "PERSON", version=2),
            _template("TPL-001", "PERSON", version=3),
        ]
        _activate_templates(client, "target-ns", templates, stats, False)

        assert client.post.call_count == 1


# ---------------------------------------------------------------------------
# Synonym tests
# ---------------------------------------------------------------------------

class TestRestoreSynonyms:

    @patch("wip_toolkit.import_.restore.console")
    def test_restore_synonyms_from_synonyms_file(self, mock_console):
        """Reads synonyms from synonyms.jsonl in archive."""
        client = _make_client()
        client.post.return_value = {
            "results": [{"status": "added"}, {"status": "already_exists"}],
        }
        stats = _make_stats()

        synonyms = [
            {"entry_id": "TERM-001", "namespace": "wip",
             "entity_type": "terminologies",
             "composite_key": {"external_code": "ISO-3166"}},
            {"entry_id": "DOC-001", "namespace": "wip",
             "entity_type": "documents",
             "composite_key": {"vendor_id": "V-001"}},
        ]
        reader = _make_reader(has_synonyms=True, synonyms=synonyms)

        _restore_synonyms(client, "target-ns", reader, stats, False)

        client.post.assert_called_once()
        batch = client.post.call_args[1]["json"]
        assert len(batch) == 2
        assert batch[0]["target_id"] == "TERM-001"
        assert stats.synonyms_registered == 2

    @patch("wip_toolkit.import_.restore.console")
    def test_restore_synonyms_empty(self, mock_console):
        """No synonyms to restore."""
        client = _make_client()
        stats = _make_stats()
        reader = _make_reader(has_synonyms=True, synonyms=[])

        _restore_synonyms(client, "target-ns", reader, stats, False)

        client.post.assert_not_called()

    @patch("wip_toolkit.import_.restore.console")
    def test_restore_synonyms_legacy_fallback(self, mock_console):
        """Falls back to _registry metadata when no synonyms.jsonl."""
        client = _make_client()
        client.post.return_value = {"results": [{"status": "added"}]}
        stats = _make_stats()

        reader = _make_reader(
            has_synonyms=False,
            terminologies=[_terminology("TERM-001", _registry={
                "primary_composite_key": {},
                "synonyms": [
                    {"namespace": "wip", "entity_type": "terminologies",
                     "composite_key": {"external_code": "ISO-3166"}},
                ],
            })],
        )

        _restore_synonyms(client, "target-ns", reader, stats, False)

        client.post.assert_called_once()
        assert stats.synonyms_registered == 1


# ---------------------------------------------------------------------------
# File upload tests
# ---------------------------------------------------------------------------

class TestUploadFiles:

    @patch("wip_toolkit.import_.restore.console")
    def test_upload_files(self, mock_console):
        client = _make_client()
        client.post_form.return_value = {"status": "created"}
        stats = _make_stats()
        reader = MagicMock()
        reader.list_blobs.return_value = ["FILE-001"]
        reader.read_blob.return_value = b"file-content"

        files = [_file("FILE-001", "doc.pdf", metadata={"description": "A doc"})]
        _upload_files(client, "target-ns", files, reader, stats, False)

        assert stats.created.files == 1
        client.post_form.assert_called_once()

    @patch("wip_toolkit.import_.restore.console")
    def test_upload_files_blob_missing(self, mock_console):
        client = _make_client()
        stats = _make_stats()
        reader = MagicMock()
        reader.list_blobs.return_value = []
        reader.read_blob.return_value = None

        files = [_file("FILE-001", "doc.pdf")]
        _upload_files(client, "target-ns", files, reader, stats, False)

        assert stats.skipped.files == 1
        client.post_form.assert_not_called()


# ---------------------------------------------------------------------------
# Skip flags tests
# ---------------------------------------------------------------------------

class TestSkipFlags:

    @patch("wip_toolkit.import_.restore.console")
    def test_skip_documents(self, mock_console):
        client = _make_client()
        reader = _make_reader(
            terminologies=[_terminology()],
            templates=[_template()],
            documents=[_document()],
        )

        def post_router(service, path, **kwargs):
            if path == "/terminologies":
                return _bulk_result(status="created")
            if path == "/templates":
                return _bulk_result(status="created")
            if path.endswith("/activate"):
                return {"status": "active"}
            return {"results": []}

        client.post.side_effect = post_router
        client.get.return_value = {"prefix": "target-ns"}

        stats = restore_import(client, reader, "target-ns", skip_documents=True)

        # Documents should NOT have been read
        entity_calls = [c[0][0] for c in reader.read_entities.call_args_list]
        assert "documents" not in entity_calls
