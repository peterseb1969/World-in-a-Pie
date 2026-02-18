"""Tests for wip_toolkit.import_.restore — restore mode import."""

from unittest.mock import MagicMock, call, patch

import pytest

from wip_toolkit.client import WIPClientError
from wip_toolkit.import_.restore import (
    _activate_templates,
    _create_documents,
    _create_templates,
    _create_terms,
    _create_terminologies,
    _ensure_namespace,
    _preregister_documents,
    _preregister_files,
    _preregister_templates,
    _preregister_terms,
    _preregister_terminologies,
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
    # Default: registry register succeeds
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
):
    """Create a mock ArchiveReader with entity data."""
    reader = MagicMock()

    terminologies = terminologies or []
    terms = terms or []
    templates = templates or []
    documents = documents or []
    files = files or []
    blobs = blobs or []

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
    return reader


def _make_stats(ns="target-ns"):
    return ImportStats(mode="restore", target_namespace=ns)


def _registry_ok(created=1, already_exists=0, errors=0, results=None):
    return {
        "created": created,
        "already_exists": already_exists,
        "errors": errors,
        "results": results or [],
    }


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
    registry=None,
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
    if registry is not None:
        base["_registry"] = registry
    base.update(overrides)
    return base


def _document(
    did="DOC-000001",
    template_id="TPL-000001",
    version=1,
    data=None,
    registry=None,
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
    if registry is not None:
        base["_registry"] = registry
    base.update(overrides)
    return base


def _file(fid="FILE-000001", filename="test.txt", registry=None, **overrides):
    base = {
        "file_id": fid,
        "filename": filename,
        "content_type": "text/plain",
        "namespace": "source-ns",
        "metadata": {},
    }
    if registry is not None:
        base["_registry"] = registry
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Full flow tests
# ---------------------------------------------------------------------------

class TestRestoreFullFlow:
    """End-to-end restore_import tests."""

    @patch("wip_toolkit.import_.restore.console")
    def test_restore_full_flow(self, mock_console):
        """Full restore with all entity types calls the right steps."""
        client = _make_client()
        terminologies = [_terminology()]
        terms = [_term()]
        templates = [_template(registry={"primary_composite_key": {}, "synonyms": []})]
        documents = [_document(registry={"primary_composite_key": {}, "synonyms": []})]
        files = [_file(fid="FILE-000001", registry={"primary_composite_key": {}, "synonyms": []})]

        reader = _make_reader(
            terminologies=terminologies,
            terms=terms,
            templates=templates,
            documents=documents,
            files=files,
            blobs=["FILE-000001"],
        )

        # Registry register calls
        client.post.return_value = _registry_ok(created=1)
        # For def-store/template-store/document-store bulk creates
        client.post.side_effect = None
        client.post.return_value = _registry_ok(created=1)

        # Need to handle different calls differently
        def post_router(service, path, **kwargs):
            if path == "/entries/register":
                return _registry_ok(created=1)
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
            return _registry_ok()

        client.post.side_effect = post_router
        client.put.return_value = _bulk_result(status="created")
        client.post_form.return_value = {"status": "created"}
        client.get.return_value = {"prefix": "target-ns"}

        stats = restore_import(client, reader, "target-ns")

        assert stats.mode == "restore"
        assert stats.target_namespace == "target-ns"
        assert stats.source_namespace == "source-ns"
        # Verify reader was asked for all entity types
        reader.read_entities.assert_any_call("terminologies")
        reader.read_entities.assert_any_call("terms")
        reader.read_entities.assert_any_call("templates")
        reader.read_entities.assert_any_call("documents")
        reader.read_entities.assert_any_call("files")

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
        # No API calls should be made
        client.get.assert_not_called()
        client.post.assert_not_called()
        client.put.assert_not_called()


# ---------------------------------------------------------------------------
# Namespace tests
# ---------------------------------------------------------------------------

class TestEnsureNamespace:

    @patch("wip_toolkit.import_.restore.console")
    def test_ensure_namespace_exists(self, mock_console):
        """When namespace already exists, no POST is made."""
        client = _make_client()
        client.get.return_value = {"prefix": "target-ns", "description": "existing"}
        stats = _make_stats()

        _ensure_namespace(client, "target-ns", stats)

        client.get.assert_called_once_with("registry", "/namespaces/target-ns")
        client.post.assert_not_called()
        assert len(stats.errors) == 0

    @patch("wip_toolkit.import_.restore.console")
    def test_ensure_namespace_created(self, mock_console):
        """When namespace returns 404, it is created via POST."""
        client = _make_client()
        client.get.side_effect = WIPClientError("Not found", status_code=404)
        client.post.return_value = {"prefix": "target-ns"}
        stats = _make_stats()

        _ensure_namespace(client, "target-ns", stats)

        client.get.assert_called_once_with("registry", "/namespaces/target-ns")
        client.post.assert_called_once_with("registry", "/namespaces", json={
            "prefix": "target-ns",
            "description": "Restored from backup",
            "isolation_mode": "open",
            "created_by": "wip-toolkit",
        })
        assert len(stats.errors) == 0

    @patch("wip_toolkit.import_.restore.console")
    def test_ensure_namespace_create_fails(self, mock_console):
        """When GET returns 404 and POST also fails, error is recorded and raised."""
        client = _make_client()
        client.get.side_effect = WIPClientError("Not found", status_code=404)
        client.post.side_effect = WIPClientError("Conflict", status_code=409)
        stats = _make_stats()

        with pytest.raises(WIPClientError, match="Conflict"):
            _ensure_namespace(client, "target-ns", stats)

        assert len(stats.errors) == 1
        assert "Failed to create namespace" in stats.errors[0]


# ---------------------------------------------------------------------------
# Pre-registration tests
# ---------------------------------------------------------------------------

class TestPreregisterTerminologies:

    @patch("wip_toolkit.import_.restore.console")
    def test_preregister_terminologies(self, mock_console):
        """Sends correct batch structure to Registry."""
        client = _make_client()
        client.post.return_value = _registry_ok(created=2)
        stats = _make_stats()
        terminologies = [
            _terminology("TERM-001", "COUNTRY", "Country"),
            _terminology("TERM-002", "STATUS", "Status"),
        ]

        _preregister_terminologies(client, "target-ns", terminologies, stats, False)

        client.post.assert_called_once()
        args, kwargs = client.post.call_args
        assert args == ("registry", "/entries/register")
        batch = kwargs["json"]
        assert len(batch) == 2
        assert batch[0]["namespace"] == "target-ns"
        assert batch[0]["entity_type"] == "terminologies"
        assert batch[0]["entry_id"] == "TERM-001"
        assert batch[0]["composite_key"] == {"value": "COUNTRY", "label": "Country"}
        assert batch[0]["created_by"] == "wip-toolkit-restore"
        assert batch[1]["entry_id"] == "TERM-002"

    @patch("wip_toolkit.import_.restore.console")
    def test_preregister_empty_list(self, mock_console):
        """Empty terminology list makes no API calls."""
        client = _make_client()
        stats = _make_stats()

        _preregister_terminologies(client, "target-ns", [], stats, False)

        client.post.assert_not_called()


class TestPreregisterTerms:

    @patch("wip_toolkit.import_.restore.console")
    def test_preregister_terms(self, mock_console):
        """Sends correct composite keys for terms."""
        client = _make_client()
        client.post.return_value = _registry_ok(created=2)
        stats = _make_stats()
        terms = [
            _term("T-001", "TERM-001", "UK"),
            _term("T-002", "TERM-001", "France"),
        ]

        _preregister_terms(client, "target-ns", terms, stats, False)

        client.post.assert_called_once()
        batch = client.post.call_args[1]["json"]
        assert len(batch) == 2
        assert batch[0]["entity_type"] == "terms"
        assert batch[0]["composite_key"] == {
            "terminology_id": "TERM-001",
            "value": "UK",
        }
        assert batch[1]["composite_key"]["value"] == "France"

    @patch("wip_toolkit.import_.restore.console")
    def test_preregister_terms_empty(self, mock_console):
        """Empty term list makes no API calls."""
        client = _make_client()
        stats = _make_stats()

        _preregister_terms(client, "target-ns", [], stats, False)

        client.post.assert_not_called()


class TestPreregisterTemplates:

    @patch("wip_toolkit.import_.restore.console")
    def test_preregister_templates_deduplicates(self, mock_console):
        """Multiple versions of same template produce one Registry entry."""
        client = _make_client()
        client.post.return_value = _registry_ok(created=1)
        stats = _make_stats()

        reg_data = {"primary_composite_key": {"value": "PERSON"}, "synonyms": []}
        templates = [
            _template("TPL-001", "PERSON", version=1, registry=reg_data),
            _template("TPL-001", "PERSON", version=2, registry=reg_data),
            _template("TPL-001", "PERSON", version=3, registry=reg_data),
        ]

        _preregister_templates(client, "target-ns", templates, stats, False)

        batch = client.post.call_args[1]["json"]
        assert len(batch) == 1
        assert batch[0]["entry_id"] == "TPL-001"
        assert batch[0]["composite_key"] == {"value": "PERSON"}

    @patch("wip_toolkit.import_.restore.console")
    def test_preregister_templates_empty(self, mock_console):
        """Empty template list makes no API calls."""
        client = _make_client()
        stats = _make_stats()

        _preregister_templates(client, "target-ns", [], stats, False)

        client.post.assert_not_called()

    @patch("wip_toolkit.import_.restore.console")
    def test_preregister_templates_no_registry_data(self, mock_console):
        """Templates without _registry use empty composite key."""
        client = _make_client()
        client.post.return_value = _registry_ok(created=1)
        stats = _make_stats()
        templates = [_template("TPL-001", "PERSON", version=1)]

        _preregister_templates(client, "target-ns", templates, stats, False)

        batch = client.post.call_args[1]["json"]
        assert len(batch) == 1
        assert batch[0]["composite_key"] == {}


class TestPreregisterDocuments:

    @patch("wip_toolkit.import_.restore.console")
    def test_preregister_documents_deduplicates(self, mock_console):
        """Multiple versions of same document produce one Registry entry."""
        client = _make_client()
        client.post.return_value = _registry_ok(created=1)
        stats = _make_stats()

        reg_data = {"primary_composite_key": {"identity_hash": "abc123"}, "synonyms": []}
        documents = [
            _document("DOC-001", version=1, registry=reg_data),
            _document("DOC-001", version=2, registry=reg_data),
        ]

        _preregister_documents(client, "target-ns", documents, stats, False)

        batch = client.post.call_args[1]["json"]
        assert len(batch) == 1
        assert batch[0]["entry_id"] == "DOC-001"
        assert batch[0]["entity_type"] == "documents"

    @patch("wip_toolkit.import_.restore.console")
    def test_preregister_documents_empty(self, mock_console):
        """Empty document list makes no API calls."""
        client = _make_client()
        stats = _make_stats()

        _preregister_documents(client, "target-ns", [], stats, False)

        client.post.assert_not_called()


# ---------------------------------------------------------------------------
# Create entities tests
# ---------------------------------------------------------------------------

class TestCreateTerminologies:

    @patch("wip_toolkit.import_.restore.console")
    def test_create_terminologies_success(self, mock_console):
        """Successfully created terminology increments stats.created."""
        client = _make_client()
        client.post.return_value = _bulk_result(status="created")
        stats = _make_stats()

        _create_terminologies(client, "target-ns", [_terminology()], stats, False)

        assert stats.created.terminologies == 1
        assert stats.skipped.terminologies == 0
        assert stats.failed.terminologies == 0

    @patch("wip_toolkit.import_.restore.console")
    def test_create_terminologies_already_exists(self, mock_console):
        """Already-existing terminology increments stats.skipped."""
        client = _make_client()
        client.post.return_value = _bulk_error("already exists")
        stats = _make_stats()

        _create_terminologies(client, "target-ns", [_terminology()], stats, True)

        assert stats.created.terminologies == 0
        assert stats.skipped.terminologies == 1
        assert stats.failed.terminologies == 0

    @patch("wip_toolkit.import_.restore.console")
    def test_create_terminologies_failure(self, mock_console):
        """Failed terminology increments stats.failed and records error."""
        client = _make_client()
        client.post.return_value = _bulk_error("validation error")
        stats = _make_stats()

        with pytest.raises(WIPClientError):
            _create_terminologies(client, "target-ns", [_terminology()], stats, False)

        assert stats.failed.terminologies == 1
        assert len(stats.errors) == 1

    @patch("wip_toolkit.import_.restore.console")
    def test_create_terminologies_payload_structure(self, mock_console):
        """Verify payload sent to Def-Store has correct structure."""
        client = _make_client()
        client.post.return_value = _bulk_result(status="created")
        stats = _make_stats()
        t = _terminology("TERM-001", "COUNTRY", "Country")

        _create_terminologies(client, "target-ns", [t], stats, False)

        args, kwargs = client.post.call_args
        assert args == ("def-store", "/terminologies")
        payload = kwargs["json"]
        assert isinstance(payload, list)
        assert len(payload) == 1
        assert payload[0]["value"] == "COUNTRY"
        assert payload[0]["label"] == "Country"
        assert payload[0]["namespace"] == "target-ns"
        assert payload[0]["created_by"] == "wip-toolkit-restore"


class TestCreateTerms:

    @patch("wip_toolkit.import_.restore.console")
    def test_create_terms_batched(self, mock_console):
        """Terms are grouped by terminology and batched by batch_size."""
        client = _make_client()
        client.post.return_value = {"succeeded": 2, "failed": 0, "results": []}
        stats = _make_stats()

        # 5 terms for TERM-001, batch_size=2 => 3 batches for TERM-001
        terms = [
            _term(f"T-{i:03d}", "TERM-001", f"value_{i}")
            for i in range(5)
        ]

        _create_terms(client, "target-ns", terms, 2, stats, False)

        # 3 batches (2, 2, 1) for terminology TERM-001
        assert client.post.call_count == 3
        # First batch has 2 terms
        first_batch = client.post.call_args_list[0][1]["json"]
        assert len(first_batch) == 2
        # Last batch has 1 term
        last_batch = client.post.call_args_list[2][1]["json"]
        assert len(last_batch) == 1
        # succeeded count: 2 per call * 3 calls = 6
        assert stats.created.terms == 6

    @patch("wip_toolkit.import_.restore.console")
    def test_create_terms_grouped_by_terminology(self, mock_console):
        """Terms from different terminologies are sent to the correct endpoints."""
        client = _make_client()
        client.post.return_value = {"succeeded": 1, "failed": 0, "results": []}
        stats = _make_stats()

        terms = [
            _term("T-001", "TERM-001", "UK"),
            _term("T-002", "TERM-002", "active"),
        ]

        _create_terms(client, "target-ns", terms, 50, stats, False)

        assert client.post.call_count == 2
        # Check the paths include the terminology_id
        call_paths = [c[0][1] for c in client.post.call_args_list]
        assert "/terminologies/TERM-001/terms" in call_paths
        assert "/terminologies/TERM-002/terms" in call_paths

    @patch("wip_toolkit.import_.restore.console")
    def test_create_terms_failure_continues(self, mock_console):
        """With continue_on_error, batch failures are recorded but don't raise."""
        client = _make_client()
        client.post.side_effect = WIPClientError("Server error", status_code=500)
        stats = _make_stats()

        terms = [_term("T-001", "TERM-001", "UK")]
        _create_terms(client, "target-ns", terms, 50, stats, True)

        assert stats.failed.terms == 1
        assert len(stats.errors) == 1


class TestCreateTemplates:

    @patch("wip_toolkit.import_.restore.console")
    def test_create_templates_version_ordered(self, mock_console):
        """v1 uses POST (create), v2 uses PUT (update)."""
        client = _make_client()
        client.post.return_value = _bulk_result(status="created")
        client.put.return_value = _bulk_result(status="created")
        stats = _make_stats()

        templates = [
            _template("TPL-001", "PERSON", version=2),
            _template("TPL-001", "PERSON", version=1),
        ]

        _create_templates(client, "target-ns", templates, stats, False)

        # v1 should be POST, v2 should be PUT (sorted by version)
        assert client.post.call_count == 1
        post_args = client.post.call_args
        assert post_args[0] == ("template-store", "/templates")
        payload = post_args[1]["json"]
        assert isinstance(payload, list)
        assert payload[0]["version"] == 1
        assert payload[0]["status"] == "draft"

        assert client.put.call_count == 1
        put_args = client.put.call_args
        assert put_args[0] == ("template-store", "/templates")
        put_payload = put_args[1]["json"]
        assert isinstance(put_payload, list)
        assert put_payload[0]["template_id"] == "TPL-001"

    @patch("wip_toolkit.import_.restore.console")
    def test_create_templates_success(self, mock_console):
        """Created templates increment stats."""
        client = _make_client()
        client.post.return_value = _bulk_result(status="created")
        stats = _make_stats()

        _create_templates(client, "target-ns", [_template()], stats, False)

        assert stats.created.templates == 1

    @patch("wip_toolkit.import_.restore.console")
    def test_create_templates_already_exists(self, mock_console):
        """Already existing templates increment skipped."""
        client = _make_client()
        client.post.return_value = _bulk_error("already exists")
        stats = _make_stats()

        _create_templates(client, "target-ns", [_template()], stats, True)

        assert stats.skipped.templates == 1
        assert stats.created.templates == 0

    @patch("wip_toolkit.import_.restore.console")
    def test_create_templates_multiple_ids(self, mock_console):
        """Multiple template IDs each get their own POST."""
        client = _make_client()
        client.post.return_value = _bulk_result(status="created")
        stats = _make_stats()

        templates = [
            _template("TPL-001", "PERSON", version=1),
            _template("TPL-002", "PROJECT", version=1),
        ]

        _create_templates(client, "target-ns", templates, stats, False)

        # Each unique template_id gets one POST
        assert client.post.call_count == 2
        assert stats.created.templates == 2


class TestCreateDocuments:

    @patch("wip_toolkit.import_.restore.console")
    def test_create_documents_batched(self, mock_console):
        """Documents respect batch_size."""
        client = _make_client()
        client.post.return_value = {"succeeded": 2, "failed": 0, "results": []}
        stats = _make_stats()

        documents = [
            _document(f"DOC-{i:03d}", version=1) for i in range(5)
        ]

        _create_documents(client, "target-ns", documents, 2, stats, False)

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

        _create_documents(client, "target-ns", documents, 50, stats, False)

        # All 3 docs in one batch; they should be sorted by version
        assert len(call_payloads) == 1
        versions = [d["version"] for d in call_payloads[0]]
        assert versions == [1, 2, 3]

    @patch("wip_toolkit.import_.restore.console")
    def test_create_documents_payload_structure(self, mock_console):
        """Verify document payload sent to Document-Store."""
        client = _make_client()
        client.post.return_value = {"succeeded": 1, "failed": 0, "results": []}
        stats = _make_stats()

        doc = _document("DOC-001", "TPL-001", version=1, data={"name": "Alice"})
        _create_documents(client, "target-ns", [doc], 50, stats, False)

        args, kwargs = client.post.call_args
        assert args == ("document-store", "/documents")
        items = kwargs["json"]
        assert len(items) == 1
        assert items[0]["document_id"] == "DOC-001"
        assert items[0]["template_id"] == "TPL-001"
        assert items[0]["namespace"] == "target-ns"
        assert items[0]["data"] == {"name": "Alice"}
        assert items[0]["created_by"] == "wip-toolkit-restore"

    @patch("wip_toolkit.import_.restore.console")
    def test_create_documents_error_recorded(self, mock_console):
        """Document errors from bulk response are recorded in stats."""
        client = _make_client()
        client.post.return_value = {
            "succeeded": 0,
            "failed": 1,
            "results": [{"index": 0, "error": "Validation failed"}],
        }
        stats = _make_stats()

        _create_documents(client, "target-ns", [_document()], 50, stats, False)

        assert stats.failed.documents == 1
        assert any("Validation failed" in e for e in stats.errors)


# ---------------------------------------------------------------------------
# Activate templates tests
# ---------------------------------------------------------------------------

class TestActivateTemplates:

    @patch("wip_toolkit.import_.restore.console")
    def test_activate_templates(self, mock_console):
        """Calls POST /templates/{id}/activate for each unique template."""
        client = _make_client()
        client.post.return_value = {"status": "active"}
        stats = _make_stats()

        templates = [
            _template("TPL-001", "PERSON", version=1),
            _template("TPL-002", "PROJECT", version=1),
        ]

        _activate_templates(client, "target-ns", templates, stats, False)

        assert client.post.call_count == 2
        calls = client.post.call_args_list
        assert calls[0] == call(
            "template-store",
            "/templates/TPL-001/activate",
            params={"namespace": "target-ns"},
        )
        assert calls[1] == call(
            "template-store",
            "/templates/TPL-002/activate",
            params={"namespace": "target-ns"},
        )

    @patch("wip_toolkit.import_.restore.console")
    def test_activate_already_active(self, mock_console):
        """400 with 'not draft' is counted as already_active, no error."""
        client = _make_client()
        client.post.side_effect = WIPClientError(
            "Template is not 'draft'", status_code=400
        )
        stats = _make_stats()

        templates = [_template("TPL-001", "PERSON", version=1)]
        _activate_templates(client, "target-ns", templates, stats, False)

        # No errors should be recorded — this is benign
        assert len(stats.errors) == 0
        # But a warning isn't added for "not draft" case either (it's silently counted)
        assert len(stats.warnings) == 0

    @patch("wip_toolkit.import_.restore.console")
    def test_activate_deduplicates_versions(self, mock_console):
        """Multiple versions of same template result in one activation call."""
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

    @patch("wip_toolkit.import_.restore.console")
    def test_activate_other_error_records_warning(self, mock_console):
        """Non-draft-related 400 error records a warning."""
        client = _make_client()
        client.post.side_effect = WIPClientError(
            "Some other issue", status_code=500
        )
        stats = _make_stats()

        templates = [_template("TPL-001", "PERSON")]
        _activate_templates(client, "target-ns", templates, stats, True)

        assert len(stats.warnings) == 1
        assert "Failed to activate template TPL-001" in stats.warnings[0]


# ---------------------------------------------------------------------------
# File tests
# ---------------------------------------------------------------------------

class TestUploadFiles:

    @patch("wip_toolkit.import_.restore.console")
    def test_upload_files(self, mock_console):
        """Reads blob from reader and calls post_form."""
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
        args, kwargs = client.post_form.call_args
        assert args == ("document-store", "/files")
        assert kwargs["data"]["namespace"] == "target-ns"
        assert kwargs["data"]["file_id"] == "FILE-001"
        # Check the file tuple: (filename, data, content_type)
        file_tuple = kwargs["files"]["file"]
        assert file_tuple[0] == "doc.pdf"
        assert file_tuple[1] == b"file-content"

    @patch("wip_toolkit.import_.restore.console")
    def test_upload_files_blob_missing(self, mock_console):
        """Files without matching blobs are skipped."""
        client = _make_client()
        stats = _make_stats()
        reader = MagicMock()
        reader.list_blobs.return_value = []  # No blobs
        reader.read_blob.return_value = None

        files = [_file("FILE-001", "doc.pdf")]

        _upload_files(client, "target-ns", files, reader, stats, False)

        assert stats.skipped.files == 1
        assert stats.created.files == 0
        client.post_form.assert_not_called()

    @patch("wip_toolkit.import_.restore.console")
    def test_upload_files_read_blob_returns_none(self, mock_console):
        """If read_blob returns None despite being listed, skip."""
        client = _make_client()
        stats = _make_stats()
        reader = MagicMock()
        reader.list_blobs.return_value = ["FILE-001"]
        reader.read_blob.return_value = None

        files = [_file("FILE-001", "doc.pdf")]

        _upload_files(client, "target-ns", files, reader, stats, False)

        assert stats.skipped.files == 1
        client.post_form.assert_not_called()

    @patch("wip_toolkit.import_.restore.console")
    def test_upload_files_with_metadata(self, mock_console):
        """File metadata (description, tags, category) is included in form data."""
        client = _make_client()
        client.post_form.return_value = {"status": "created"}
        stats = _make_stats()
        reader = MagicMock()
        reader.list_blobs.return_value = ["FILE-001"]
        reader.read_blob.return_value = b"content"

        files = [_file("FILE-001", "photo.jpg", metadata={
            "description": "A photo",
            "tags": ["nature", "landscape"],
            "category": "photos",
        })]

        _upload_files(client, "target-ns", files, reader, stats, False)

        form_data = client.post_form.call_args[1]["data"]
        assert form_data["description"] == "A photo"
        assert form_data["tags"] == "nature,landscape"
        assert form_data["category"] == "photos"


# ---------------------------------------------------------------------------
# Synonym tests
# ---------------------------------------------------------------------------

class TestRestoreSynonyms:

    @patch("wip_toolkit.import_.restore.console")
    def test_restore_synonyms(self, mock_console):
        """Posts synonym batch to Registry."""
        client = _make_client()
        client.post.return_value = {
            "results": [
                {"status": "added"},
                {"status": "already_exists"},
            ],
        }
        stats = _make_stats()

        entities_by_type = {
            "terminologies": [
                _terminology("TERM-001", _registry={
                    "primary_composite_key": {},
                    "synonyms": [
                        {"namespace": "wip", "entity_type": "terminologies",
                         "composite_key": {"external_code": "ISO-3166"}},
                        {"namespace": "wip", "entity_type": "terminologies",
                         "composite_key": {"vendor_id": "V-001"}},
                    ],
                }),
            ],
            "terms": [], "templates": [], "documents": [], "files": [],
        }

        _restore_synonyms(client, "target-ns", entities_by_type, stats, False)

        client.post.assert_called_once()
        args, kwargs = client.post.call_args
        assert args == ("registry", "/synonyms/add")
        batch = kwargs["json"]
        assert len(batch) == 2
        assert batch[0]["target_id"] == "TERM-001"
        assert batch[0]["synonym_composite_key"] == {"external_code": "ISO-3166"}
        assert batch[1]["synonym_composite_key"] == {"vendor_id": "V-001"}
        assert stats.synonyms_registered == 2

    @patch("wip_toolkit.import_.restore.console")
    def test_restore_synonyms_deduplicates(self, mock_console):
        """Same entity_id across versions produces one synonym batch entry."""
        client = _make_client()
        client.post.return_value = {
            "results": [{"status": "added"}],
        }
        stats = _make_stats()

        reg_data = {
            "primary_composite_key": {},
            "synonyms": [
                {"namespace": "wip", "entity_type": "templates",
                 "composite_key": {"alias": "person-tpl"}},
            ],
        }
        entities_by_type = {
            "terminologies": [], "terms": [],
            "templates": [
                _template("TPL-001", "PERSON", version=1, registry=reg_data),
                _template("TPL-001", "PERSON", version=2, registry=reg_data),
                _template("TPL-001", "PERSON", version=3, registry=reg_data),
            ],
            "documents": [], "files": [],
        }

        _restore_synonyms(client, "target-ns", entities_by_type, stats, False)

        batch = client.post.call_args[1]["json"]
        # Only one synonym item despite 3 versions
        assert len(batch) == 1
        assert batch[0]["target_id"] == "TPL-001"

    @patch("wip_toolkit.import_.restore.console")
    def test_restore_synonyms_empty(self, mock_console):
        """Entities with no _registry data produce no API call."""
        client = _make_client()
        stats = _make_stats()

        entities_by_type = {
            "terminologies": [_terminology("TERM-001")],
            "terms": [],
            "templates": [_template("TPL-001")],
            "documents": [], "files": [],
        }

        _restore_synonyms(client, "target-ns", entities_by_type, stats, False)

        client.post.assert_not_called()
        assert stats.synonyms_registered == 0

    @patch("wip_toolkit.import_.restore.console")
    def test_restore_synonyms_empty_synonym_list(self, mock_console):
        """Entity with _registry but empty synonyms list produces no API call."""
        client = _make_client()
        stats = _make_stats()

        entities_by_type = {
            "terminologies": [
                _terminology("TERM-001", _registry={
                    "primary_composite_key": {"value": "COUNTRY"},
                    "synonyms": [],
                }),
            ],
            "terms": [], "templates": [], "documents": [], "files": [],
        }

        _restore_synonyms(client, "target-ns", entities_by_type, stats, False)

        client.post.assert_not_called()

    @patch("wip_toolkit.import_.restore.console")
    def test_restore_synonyms_multiple_entity_types(self, mock_console):
        """Synonyms from different entity types are collected into one batch."""
        client = _make_client()
        client.post.return_value = {
            "results": [
                {"status": "added"},
                {"status": "added"},
            ],
        }
        stats = _make_stats()

        entities_by_type = {
            "terminologies": [
                _terminology("TERM-001", _registry={
                    "primary_composite_key": {},
                    "synonyms": [
                        {"namespace": "wip", "entity_type": "terminologies",
                         "composite_key": {"code": "X"}},
                    ],
                }),
            ],
            "terms": [], "templates": [],
            "documents": [
                _document("DOC-001", registry={
                    "primary_composite_key": {},
                    "synonyms": [
                        {"namespace": "wip", "entity_type": "documents",
                         "composite_key": {"vendor": "V1"}},
                    ],
                }),
            ],
            "files": [],
        }

        _restore_synonyms(client, "target-ns", entities_by_type, stats, False)

        batch = client.post.call_args[1]["json"]
        assert len(batch) == 2
        target_ids = {item["target_id"] for item in batch}
        assert target_ids == {"TERM-001", "DOC-001"}


# ---------------------------------------------------------------------------
# Skip flags tests
# ---------------------------------------------------------------------------

class TestSkipFlags:

    @patch("wip_toolkit.import_.restore.console")
    def test_skip_documents(self, mock_console):
        """With skip_documents=True, no document pre-registration or creation."""
        client = _make_client()
        reader = _make_reader(
            terminologies=[_terminology()],
            templates=[_template(registry={"primary_composite_key": {}, "synonyms": []})],
            documents=[_document()],
        )

        def post_router(service, path, **kwargs):
            if path == "/entries/register":
                return _registry_ok(created=1)
            if path == "/terminologies":
                return _bulk_result(status="created")
            if path == "/templates":
                return _bulk_result(status="created")
            if path.endswith("/activate"):
                return {"status": "active"}
            if path == "/synonyms/add":
                return {"results": []}
            return _registry_ok()

        client.post.side_effect = post_router
        client.get.return_value = {"prefix": "target-ns"}

        stats = restore_import(client, reader, "target-ns", skip_documents=True)

        # Documents should NOT have been read
        entity_calls = [c[0][0] for c in reader.read_entities.call_args_list]
        assert "documents" not in entity_calls

        # No document-store calls for documents
        for c in client.post.call_args_list:
            path = c[0][1]
            assert path != "/documents"

    @patch("wip_toolkit.import_.restore.console")
    def test_skip_files(self, mock_console):
        """With skip_files=True, no file pre-registration or upload."""
        client = _make_client()
        reader = _make_reader(
            terminologies=[_terminology()],
            templates=[_template(registry={"primary_composite_key": {}, "synonyms": []})],
            documents=[_document(registry={"primary_composite_key": {}, "synonyms": []})],
            files=[_file()],
        )

        def post_router(service, path, **kwargs):
            if path == "/entries/register":
                return _registry_ok(created=1)
            if path == "/terminologies":
                return _bulk_result(status="created")
            if path == "/templates":
                return _bulk_result(status="created")
            if path.endswith("/activate"):
                return {"status": "active"}
            if path == "/documents":
                return {"succeeded": 1, "failed": 0, "results": []}
            if path == "/synonyms/add":
                return {"results": []}
            return _registry_ok()

        client.post.side_effect = post_router
        client.get.return_value = {"prefix": "target-ns"}

        stats = restore_import(client, reader, "target-ns", skip_files=True)

        # Files should NOT have been read
        entity_calls = [c[0][0] for c in reader.read_entities.call_args_list]
        assert "files" not in entity_calls

        # No post_form calls for file uploads
        client.post_form.assert_not_called()


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------

class TestErrorHandling:

    @patch("wip_toolkit.import_.restore.console")
    def test_continue_on_error(self, mock_console):
        """With continue_on_error, failures are recorded but execution continues."""
        client = _make_client()

        call_count = 0

        def post_router(service, path, **kwargs):
            nonlocal call_count
            call_count += 1
            if path == "/entries/register":
                return _registry_ok(created=1)
            if path == "/terminologies":
                # Terminology creation fails
                return _bulk_error("server error")
            if path.startswith("/terminologies/") and "/terms" in path:
                raise WIPClientError("Term creation failed", status_code=500)
            if path == "/templates":
                return _bulk_result(status="created")
            if path.endswith("/activate"):
                return {"status": "active"}
            if path == "/documents":
                return {"succeeded": 0, "failed": 1,
                        "results": [{"index": 0, "error": "Doc validation error"}]}
            if path == "/synonyms/add":
                return {"results": []}
            return _registry_ok()

        client.post.side_effect = post_router
        client.get.return_value = {"prefix": "target-ns"}

        reader = _make_reader(
            terminologies=[_terminology()],
            terms=[_term()],
            templates=[_template(registry={"primary_composite_key": {}, "synonyms": []})],
            documents=[_document(registry={"primary_composite_key": {}, "synonyms": []})],
        )

        # Should NOT raise despite multiple failures
        stats = restore_import(
            client, reader, "target-ns",
            continue_on_error=True,
        )

        # Errors should be recorded
        assert len(stats.errors) > 0
        assert stats.failed.terminologies >= 1

    @patch("wip_toolkit.import_.restore.console")
    def test_registry_batch_error_propagates(self, mock_console):
        """Without continue_on_error, registry batch error raises."""
        client = _make_client()
        client.post.side_effect = WIPClientError("Registry down", status_code=503)
        stats = _make_stats()

        with pytest.raises(WIPClientError, match="Registry down"):
            _preregister_terminologies(
                client, "target-ns", [_terminology()], stats, False
            )

    @patch("wip_toolkit.import_.restore.console")
    def test_registry_batch_error_continue(self, mock_console):
        """With continue_on_error, registry batch error is recorded."""
        client = _make_client()
        client.post.side_effect = WIPClientError("Registry down", status_code=503)
        stats = _make_stats()

        _preregister_terminologies(
            client, "target-ns", [_terminology()], stats, True
        )

        assert len(stats.errors) == 1
        assert "Batch registration failed" in stats.errors[0]

    @patch("wip_toolkit.import_.restore.console")
    def test_upload_file_failure_continue(self, mock_console):
        """File upload failure with continue_on_error records error."""
        client = _make_client()
        client.post_form.side_effect = WIPClientError("Upload failed", status_code=500)
        stats = _make_stats()
        reader = MagicMock()
        reader.list_blobs.return_value = ["FILE-001"]
        reader.read_blob.return_value = b"data"

        files = [_file("FILE-001", "doc.pdf")]
        _upload_files(client, "target-ns", files, reader, stats, True)

        assert stats.failed.files == 1
        assert stats.created.files == 0
        assert any("Failed to upload file FILE-001" in e for e in stats.errors)

    @patch("wip_toolkit.import_.restore.console")
    def test_upload_file_failure_raises(self, mock_console):
        """File upload failure without continue_on_error raises."""
        client = _make_client()
        client.post_form.side_effect = WIPClientError("Upload failed", status_code=500)
        stats = _make_stats()
        reader = MagicMock()
        reader.list_blobs.return_value = ["FILE-001"]
        reader.read_blob.return_value = b"data"

        files = [_file("FILE-001", "doc.pdf")]

        with pytest.raises(WIPClientError, match="Upload failed"):
            _upload_files(client, "target-ns", files, reader, stats, False)


# ---------------------------------------------------------------------------
# Pre-register files tests
# ---------------------------------------------------------------------------

class TestPreregisterFiles:

    @patch("wip_toolkit.import_.restore.console")
    def test_preregister_files(self, mock_console):
        """File IDs are registered with composite keys from _registry."""
        client = _make_client()
        client.post.return_value = _registry_ok(created=1)
        stats = _make_stats()

        reg_data = {"primary_composite_key": {"filename": "doc.pdf"}, "synonyms": []}
        files = [_file("FILE-001", "doc.pdf", registry=reg_data)]

        _preregister_files(client, "target-ns", files, stats, False)

        batch = client.post.call_args[1]["json"]
        assert len(batch) == 1
        assert batch[0]["entry_id"] == "FILE-001"
        assert batch[0]["entity_type"] == "files"
        assert batch[0]["composite_key"] == {"filename": "doc.pdf"}

    @patch("wip_toolkit.import_.restore.console")
    def test_preregister_files_empty(self, mock_console):
        """Empty file list makes no API calls."""
        client = _make_client()
        stats = _make_stats()

        _preregister_files(client, "target-ns", [], stats, False)

        client.post.assert_not_called()
