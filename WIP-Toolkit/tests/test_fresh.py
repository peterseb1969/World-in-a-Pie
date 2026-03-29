"""Tests for fresh-mode import (new IDs, remapped references)."""

from unittest.mock import MagicMock

import pytest
from wip_toolkit.client import WIPClientError
from wip_toolkit.import_.fresh import (
    _create_documents,
    _create_templates_multipass,
    _create_terminologies,
    _create_terms,
    _ensure_namespace,
    _register_synonyms,
    _remap_composite_key,
    _remap_id,
    _upload_files,
    fresh_import,
)
from wip_toolkit.import_.remap import IDRemapper
from wip_toolkit.models import ImportStats

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manifest(namespace="source-ns"):
    """Return a mock manifest object."""
    m = MagicMock()
    m.namespace = namespace
    return m


def _make_reader(
    manifest=None,
    terminologies=None,
    terms=None,
    templates=None,
    documents=None,
    files=None,
    blobs=None,
):
    """Build a mock ArchiveReader with configurable entity iterators."""
    reader = MagicMock()
    reader.read_manifest.return_value = manifest or _make_manifest()

    entity_map = {
        "terminologies": terminologies or [],
        "terms": terms or [],
        "templates": templates or [],
        "documents": documents or [],
        "files": files or [],
    }
    reader.read_entities.side_effect = lambda kind: iter(entity_map.get(kind, []))
    reader.list_blobs.return_value = blobs or []
    reader.read_blob.return_value = b"fake-content"
    return reader


def _ok_terminology(new_id):
    return {
        "results": [{"index": 0, "status": "created", "id": new_id}],
        "succeeded": 1,
        "failed": 0,
    }


def _ok_terms(id_pairs):
    """Build a bulk-term creation response. id_pairs = [(index, new_id), ...]"""
    return {
        "results": [
            {"index": idx, "status": "created", "id": nid}
            for idx, nid in id_pairs
        ],
        "succeeded": len(id_pairs),
        "failed": 0,
    }


def _ok_template(new_id):
    return {
        "results": [{"index": 0, "status": "created", "id": new_id}],
        "succeeded": 1,
        "failed": 0,
    }


def _ok_documents(id_pairs):
    """id_pairs = [(index, new_doc_id), ...]"""
    return {
        "results": [
            {"index": idx, "status": "created", "document_id": nid}
            for idx, nid in id_pairs
        ],
        "succeeded": len(id_pairs),
        "failed": 0,
    }


def _ok_file(new_file_id):
    return {"file_id": new_file_id}


def _ok_synonyms(count):
    return {
        "results": [{"status": "added"} for _ in range(count)],
    }


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------

class TestRemapId:
    """Tests for _remap_id helper."""

    def test_found_in_first_map(self):
        maps = [{"OLD-1": "NEW-1"}, {"OLD-2": "NEW-2"}]
        assert _remap_id("OLD-1", maps) == "NEW-1"

    def test_found_in_second_map(self):
        maps = [{"A": "B"}, {"OLD-1": "NEW-1"}]
        assert _remap_id("OLD-1", maps) == "NEW-1"

    def test_not_found_returns_original(self):
        maps = [{"A": "B"}, {"C": "D"}]
        assert _remap_id("MISSING", maps) == "MISSING"

    def test_empty_maps(self):
        assert _remap_id("X", []) == "X"

    def test_first_match_wins(self):
        maps = [{"K": "V1"}, {"K": "V2"}]
        assert _remap_id("K", maps) == "V1"


class TestRemapCompositeKey:
    """Tests for _remap_composite_key helper."""

    def test_remaps_string_values(self):
        maps = [{"OLD-TPL": "NEW-TPL"}, {"OLD-TERM": "NEW-TERM"}]
        ck = {"template_id": "OLD-TPL", "namespace": "wip"}
        result = _remap_composite_key(ck, maps)
        assert result["template_id"] == "NEW-TPL"
        # Non-ID strings are looked up too; "wip" not in maps so unchanged
        assert result["namespace"] == "wip"

    def test_non_string_values_passthrough(self):
        maps = [{"OLD": "NEW"}]
        ck = {"count": 42, "flag": True, "id": "OLD"}
        result = _remap_composite_key(ck, maps)
        assert result["count"] == 42
        assert result["flag"] is True
        assert result["id"] == "NEW"

    def test_empty_composite_key(self):
        assert _remap_composite_key({}, [{"A": "B"}]) == {}

    def test_no_maps(self):
        ck = {"id": "X"}
        assert _remap_composite_key(ck, []) == {"id": "X"}


# ---------------------------------------------------------------------------
# _ensure_namespace
# ---------------------------------------------------------------------------

class TestEnsureNamespace:
    def test_namespace_exists(self):
        client = MagicMock()
        client.get.return_value = {"prefix": "target-ns"}
        stats = ImportStats(mode="fresh", target_namespace="target-ns")

        _ensure_namespace(client, "target-ns", stats)

        client.get.assert_called_once_with("registry", "/namespaces/target-ns")
        client.post.assert_not_called()

    def test_namespace_404_creates(self):
        client = MagicMock()
        client.get.side_effect = WIPClientError("Not found", status_code=404)
        client.post.return_value = {"prefix": "target-ns"}
        stats = ImportStats(mode="fresh", target_namespace="target-ns")

        _ensure_namespace(client, "target-ns", stats)

        client.post.assert_called_once()
        args, kwargs = client.post.call_args
        assert args == ("registry", "/namespaces")
        assert kwargs["json"]["prefix"] == "target-ns"

    def test_namespace_create_fails(self):
        client = MagicMock()
        client.get.side_effect = WIPClientError("Not found", status_code=404)
        client.post.side_effect = WIPClientError("Conflict", status_code=409)
        stats = ImportStats(mode="fresh", target_namespace="target-ns")

        with pytest.raises(WIPClientError):
            _ensure_namespace(client, "target-ns", stats)
        assert len(stats.errors) == 1

    def test_namespace_other_error_raises(self):
        client = MagicMock()
        client.get.side_effect = WIPClientError("Server Error", status_code=500)
        stats = ImportStats(mode="fresh", target_namespace="target-ns")

        with pytest.raises(WIPClientError):
            _ensure_namespace(client, "target-ns", stats)


# ---------------------------------------------------------------------------
# _create_terminologies
# ---------------------------------------------------------------------------

class TestCreateTerminologies:
    def test_create_terminologies_maps_ids(self):
        client = MagicMock()
        client.post.return_value = _ok_terminology("NEW-TERM-001")
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        terminologies = [
            {"terminology_id": "OLD-TERM-001", "value": "COUNTRY", "label": "Country"},
        ]

        _create_terminologies(client, "ns", terminologies, remapper, stats, False)

        assert remapper.terminology_map["OLD-TERM-001"] == "NEW-TERM-001"
        assert stats.created.terminologies == 1

    def test_create_terminologies_already_exists(self):
        client = MagicMock()
        client.post.return_value = {
            "results": [{"index": 0, "status": "error", "error": "already exists"}],
            "succeeded": 0,
            "failed": 1,
        }
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        terminologies = [
            {"terminology_id": "OLD-TERM-001", "value": "COUNTRY"},
        ]

        _create_terminologies(client, "ns", terminologies, remapper, stats, False)

        assert stats.skipped.terminologies == 1
        assert stats.created.terminologies == 0
        assert len(remapper.terminology_map) == 0

    def test_create_terminologies_error(self):
        client = MagicMock()
        client.post.return_value = {
            "results": [{"index": 0, "status": "error", "error": "validation failed"}],
            "succeeded": 0,
            "failed": 1,
        }
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        terminologies = [
            {"terminology_id": "OLD-TERM-001", "value": "BAD"},
        ]

        with pytest.raises(WIPClientError):
            _create_terminologies(client, "ns", terminologies, remapper, stats, False)

        assert stats.failed.terminologies == 1
        assert len(stats.errors) == 1

    def test_create_terminologies_error_continue(self):
        """With continue_on_error, failure recorded but no raise."""
        client = MagicMock()
        client.post.return_value = {
            "results": [{"index": 0, "status": "error", "error": "validation failed"}],
            "succeeded": 0,
            "failed": 1,
        }
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        terminologies = [
            {"terminology_id": "OLD-TERM-001", "value": "BAD"},
        ]

        # Should NOT raise
        _create_terminologies(client, "ns", terminologies, remapper, stats, True)
        assert stats.failed.terminologies == 1

    def test_create_multiple_terminologies(self):
        client = MagicMock()
        client.post.side_effect = [
            _ok_terminology("NEW-TERM-A"),
            _ok_terminology("NEW-TERM-B"),
        ]
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        terminologies = [
            {"terminology_id": "OLD-A", "value": "A"},
            {"terminology_id": "OLD-B", "value": "B"},
        ]

        _create_terminologies(client, "ns", terminologies, remapper, stats, False)

        assert remapper.terminology_map == {"OLD-A": "NEW-TERM-A", "OLD-B": "NEW-TERM-B"}
        assert stats.created.terminologies == 2


# ---------------------------------------------------------------------------
# _create_terms
# ---------------------------------------------------------------------------

class TestCreateTerms:
    def test_create_terms_uses_remapped_terminology_id(self):
        """Terms are posted to /terminologies/{NEW_TID}/terms."""
        client = MagicMock()
        client.post.return_value = _ok_terms([(0, "NEW-T-001")])
        remapper = IDRemapper()
        remapper.add_terminology_mapping("OLD-TID", "NEW-TID")
        stats = ImportStats(mode="fresh", target_namespace="ns")
        terms = [
            {"term_id": "OLD-T-001", "terminology_id": "OLD-TID", "value": "UK"},
        ]

        _create_terms(client, "ns", terms, remapper, 50, stats, False)

        args, _kwargs = client.post.call_args
        assert args == ("def-store", "/terminologies/NEW-TID/terms")

    def test_create_terms_maps_ids(self):
        client = MagicMock()
        client.post.return_value = _ok_terms([(0, "NEW-T-001"), (1, "NEW-T-002")])
        remapper = IDRemapper()
        remapper.add_terminology_mapping("OLD-TID", "NEW-TID")
        stats = ImportStats(mode="fresh", target_namespace="ns")
        terms = [
            {"term_id": "OLD-T-001", "terminology_id": "OLD-TID", "value": "UK"},
            {"term_id": "OLD-T-002", "terminology_id": "OLD-TID", "value": "France"},
        ]

        _create_terms(client, "ns", terms, remapper, 50, stats, False)

        assert remapper.term_map["OLD-T-001"] == "NEW-T-001"
        assert remapper.term_map["OLD-T-002"] == "NEW-T-002"
        assert stats.created.terms == 2

    def test_create_terms_unmapped_terminology_skipped(self):
        """Terms whose terminology has no mapping are skipped with warning."""
        client = MagicMock()
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        terms = [
            {"term_id": "T-001", "terminology_id": "UNMAPPED-TID", "value": "foo"},
            {"term_id": "T-002", "terminology_id": "UNMAPPED-TID", "value": "bar"},
        ]

        _create_terms(client, "ns", terms, remapper, 50, stats, False)

        client.post.assert_not_called()
        assert stats.skipped.terms == 2
        assert len(stats.warnings) == 1
        assert "UNMAPPED-TID" in stats.warnings[0]

    def test_create_terms_remaps_parent_term_id(self):
        """parent_term_id is remapped through the term_map."""
        client = MagicMock()
        client.post.return_value = _ok_terms([(0, "NEW-CHILD")])
        remapper = IDRemapper()
        remapper.add_terminology_mapping("TID-1", "NEW-TID-1")
        remapper.add_term_mapping("OLD-PARENT", "NEW-PARENT")
        stats = ImportStats(mode="fresh", target_namespace="ns")
        terms = [
            {
                "term_id": "OLD-CHILD",
                "terminology_id": "TID-1",
                "value": "Child",
                "parent_term_id": "OLD-PARENT",
            },
        ]

        _create_terms(client, "ns", terms, remapper, 50, stats, False)

        payload = client.post.call_args[1]["json"]
        assert payload[0]["parent_term_id"] == "NEW-PARENT"

    def test_create_terms_batched(self):
        """Terms are sent in batches respecting batch_size."""
        client = MagicMock()
        # Two batches: first 2, then 1
        client.post.side_effect = [
            _ok_terms([(0, "N-1"), (1, "N-2")]),
            _ok_terms([(0, "N-3")]),
        ]
        remapper = IDRemapper()
        remapper.add_terminology_mapping("TID", "NEW-TID")
        stats = ImportStats(mode="fresh", target_namespace="ns")
        terms = [
            {"term_id": f"T-{i}", "terminology_id": "TID", "value": f"v{i}"}
            for i in range(3)
        ]

        _create_terms(client, "ns", terms, remapper, batch_size=2, stats=stats, continue_on_error=False)

        assert client.post.call_count == 2
        # First batch has 2 items, second has 1
        first_payload = client.post.call_args_list[0][1]["json"]
        second_payload = client.post.call_args_list[1][1]["json"]
        assert len(first_payload) == 2
        assert len(second_payload) == 1
        assert stats.created.terms == 3

    def test_create_terms_api_error_continue(self):
        """API error with continue_on_error records failure."""
        client = MagicMock()
        client.post.side_effect = WIPClientError("timeout", status_code=504)
        remapper = IDRemapper()
        remapper.add_terminology_mapping("TID", "NEW-TID")
        stats = ImportStats(mode="fresh", target_namespace="ns")
        terms = [
            {"term_id": "T-1", "terminology_id": "TID", "value": "v1"},
        ]

        _create_terms(client, "ns", terms, remapper, 50, stats, continue_on_error=True)

        assert stats.failed.terms == 1
        assert len(stats.errors) == 1


# ---------------------------------------------------------------------------
# _create_templates_multipass
# ---------------------------------------------------------------------------

class TestCreateTemplatesMultipass:
    def test_create_templates_remaps_references(self):
        """remap_template is called (indirectly) via remapper."""
        client = MagicMock()
        # POST for creation, then POST for activation
        client.post.side_effect = [
            _ok_template("NEW-TPL-001"),
            {},  # activation
        ]
        remapper = IDRemapper()
        remapper.add_terminology_mapping("OLD-TERM", "NEW-TERM")
        stats = ImportStats(mode="fresh", target_namespace="ns")
        templates = [
            {
                "template_id": "OLD-TPL-001",
                "value": "PERSON",
                "version": 1,
                "fields": [
                    {"name": "country", "type": "term", "terminology_ref": "OLD-TERM"},
                ],
            },
        ]

        _create_templates_multipass(client, "ns", templates, remapper, stats, False)

        # First POST is the template creation
        create_payload = client.post.call_args_list[0][1]["json"]
        assert create_payload[0]["fields"][0]["terminology_ref"] == "NEW-TERM"

    def test_create_templates_first_version_post(self):
        """First version uses POST with status=draft."""
        client = MagicMock()
        client.post.side_effect = [
            _ok_template("NEW-TPL"),
            {},  # activation
        ]
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        templates = [
            {"template_id": "OLD-TPL", "value": "THING", "version": 1, "fields": []},
        ]

        _create_templates_multipass(client, "ns", templates, remapper, stats, False)

        # First POST is template creation
        args, kwargs = client.post.call_args_list[0]
        assert args == ("template-store", "/templates")
        assert kwargs["json"][0]["status"] == "draft"
        assert kwargs["json"][0]["namespace"] == "ns"

    def test_create_templates_subsequent_version_put(self):
        """Subsequent versions use PUT with the new template_id."""
        client = MagicMock()
        # POST v1, then POST activate
        client.post.side_effect = [
            _ok_template("NEW-TPL"),
            {},  # activation
        ]
        # PUT v2
        client.put.return_value = {
            "results": [{"index": 0, "status": "created", "id": "NEW-TPL"}],
            "succeeded": 1,
            "failed": 0,
        }
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        templates = [
            {"template_id": "OLD-TPL", "value": "THING", "version": 1, "fields": []},
            {"template_id": "OLD-TPL", "value": "THING", "version": 2, "fields": []},
        ]

        _create_templates_multipass(client, "ns", templates, remapper, stats, False)

        # POST: v1 creation + activation = 2
        assert client.post.call_count == 2
        # PUT for version 2
        assert client.put.call_count == 1
        put_args, put_kwargs = client.put.call_args
        assert put_args == ("template-store", "/templates")
        assert put_kwargs["json"][0]["template_id"] == "NEW-TPL"
        assert stats.created.templates == 2

    def test_create_templates_maps_ids(self):
        client = MagicMock()
        client.post.side_effect = [
            _ok_template("NEW-TPL-001"),
            {},  # activation
        ]
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        templates = [
            {"template_id": "OLD-TPL-001", "value": "THING", "version": 1, "fields": []},
        ]

        _create_templates_multipass(client, "ns", templates, remapper, stats, False)

        assert remapper.template_map["OLD-TPL-001"] == "NEW-TPL-001"

    def test_create_templates_error_first_version(self):
        """Error on first version is recorded."""
        client = MagicMock()
        client.post.return_value = {
            "results": [{"index": 0, "status": "error", "error": "bad fields"}],
            "succeeded": 0,
            "failed": 1,
        }
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        templates = [
            {"template_id": "OLD-TPL", "value": "BAD", "version": 1, "fields": []},
        ]

        with pytest.raises(WIPClientError):
            _create_templates_multipass(client, "ns", templates, remapper, stats, False)

        assert stats.failed.templates == 1

    def test_create_templates_extends_remapped(self):
        """extends field is remapped to new parent template_id via multi-pass."""
        client = MagicMock()
        # Pass 1: parent created + activated, Pass 2: child created + activated
        client.post.side_effect = [
            _ok_template("NEW-PARENT"),
            _ok_template("NEW-CHILD"),
            {},  # activate parent
            {},  # activate child
        ]
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        templates = [
            {"template_id": "OLD-PARENT", "value": "PARENT", "version": 1, "extends": None, "fields": []},
            {"template_id": "OLD-CHILD", "value": "CHILD", "version": 1, "extends": "OLD-PARENT", "fields": []},
        ]

        _create_templates_multipass(client, "ns", templates, remapper, stats, False)

        # Find the child creation POST (second template-store /templates POST)
        create_posts = [
            c for c in client.post.call_args_list
            if c[0] == ("template-store", "/templates")
        ]
        assert len(create_posts) == 2
        child_payload = create_posts[1][1]["json"]
        assert child_payload[0]["extends"] == "NEW-PARENT"

    def test_activate_already_active(self):
        """400 'not draft' is counted as already active, not an error."""
        client = MagicMock()
        client.post.side_effect = [
            _ok_template("NEW-TPL"),
            # activation returns "not draft"
            WIPClientError("Template status is 'active', not 'draft'", status_code=400),
        ]
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        templates = [
            {"template_id": "OLD-TPL", "value": "THING", "version": 1, "fields": []},
        ]

        # Should not raise
        _create_templates_multipass(client, "ns", templates, remapper, stats, False)

        # No error recorded in stats.warnings
        assert len(stats.warnings) == 0

    def test_activate_deduplicates(self):
        """Same template_id (different versions) only activated once."""
        client = MagicMock()
        client.post.side_effect = [
            _ok_template("NEW-TPL"),
            {},  # single activation
        ]
        client.put.return_value = {
            "results": [{"index": 0, "status": "created", "id": "NEW-TPL"}],
            "succeeded": 1,
            "failed": 0,
        }
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        templates = [
            {"template_id": "OLD-TPL", "value": "THING", "version": 1, "fields": []},
            {"template_id": "OLD-TPL", "value": "THING", "version": 2, "fields": []},
        ]

        _create_templates_multipass(client, "ns", templates, remapper, stats, False)

        # Activation calls: only 1 (deduplicated across versions)
        activate_calls = [
            c for c in client.post.call_args_list
            if "/activate" in str(c)
        ]
        assert len(activate_calls) == 1

    def test_activate_error_raises(self):
        """Non-400 activation errors raise when continue_on_error=False."""
        client = MagicMock()
        client.post.side_effect = [
            _ok_template("NEW-TPL"),
            WIPClientError("Server error", status_code=500),  # activation fails
        ]
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        templates = [
            {"template_id": "OLD-TPL", "value": "THING", "version": 1, "fields": []},
        ]

        with pytest.raises(WIPClientError):
            _create_templates_multipass(client, "ns", templates, remapper, stats, False)

    def test_activate_error_continue(self):
        """Non-400 activation errors recorded in warnings with continue_on_error=True."""
        client = MagicMock()
        client.post.side_effect = [
            _ok_template("NEW-TPL"),
            WIPClientError("Server error", status_code=500),  # activation fails
        ]
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        templates = [
            {"template_id": "OLD-TPL", "value": "THING", "version": 1, "fields": []},
        ]

        _create_templates_multipass(client, "ns", templates, remapper, stats, True)

        assert len(stats.warnings) == 1


# ---------------------------------------------------------------------------
# _create_documents
# ---------------------------------------------------------------------------

class TestCreateDocuments:
    def test_create_documents_remaps_references(self):
        """Documents have template_id and term references remapped."""
        client = MagicMock()
        client.post.return_value = _ok_documents([(0, "NEW-DOC-001")])
        remapper = IDRemapper()
        remapper.add_template_mapping("OLD-TPL", "NEW-TPL")
        remapper.add_term_mapping("OLD-T", "NEW-T")
        stats = ImportStats(mode="fresh", target_namespace="ns")
        documents = [
            {
                "document_id": "OLD-DOC-001",
                "template_id": "OLD-TPL",
                "version": 1,
                "data": {"name": "Test"},
                "term_references": [
                    {"field_path": "status", "term_id": "OLD-T", "terminology_ref": "SOME-TREF"},
                ],
            },
        ]

        _create_documents(client, "ns", documents, remapper, 50, stats, False)

        payload = client.post.call_args[1]["json"]
        assert payload[0]["template_id"] == "NEW-TPL"

    def test_create_documents_maps_ids(self):
        client = MagicMock()
        client.post.return_value = _ok_documents([(0, "NEW-DOC-001")])
        remapper = IDRemapper()
        remapper.add_template_mapping("OLD-TPL", "NEW-TPL")
        stats = ImportStats(mode="fresh", target_namespace="ns")
        documents = [
            {
                "document_id": "OLD-DOC-001",
                "template_id": "OLD-TPL",
                "version": 1,
                "data": {"x": 1},
            },
        ]

        _create_documents(client, "ns", documents, remapper, 50, stats, False)

        assert remapper.document_map["OLD-DOC-001"] == "NEW-DOC-001"
        assert stats.created.documents == 1

    def test_create_documents_batched(self):
        """Documents are sent in batches respecting batch_size."""
        client = MagicMock()
        client.post.side_effect = [
            _ok_documents([(0, "N-1"), (1, "N-2")]),
            _ok_documents([(0, "N-3")]),
        ]
        remapper = IDRemapper()
        remapper.add_template_mapping("TPL", "NEW-TPL")
        stats = ImportStats(mode="fresh", target_namespace="ns")
        documents = [
            {"document_id": f"D-{i}", "template_id": "TPL", "version": 1, "data": {}}
            for i in range(3)
        ]

        _create_documents(client, "ns", documents, remapper, batch_size=2, stats=stats, continue_on_error=False)

        assert client.post.call_count == 2
        first_payload = client.post.call_args_list[0][1]["json"]
        second_payload = client.post.call_args_list[1][1]["json"]
        assert len(first_payload) == 2
        assert len(second_payload) == 1
        assert stats.created.documents == 3

    def test_create_documents_error_recorded(self):
        """Individual document errors in results are recorded."""
        client = MagicMock()
        # Pass 1: D-1 succeeds, D-2 fails. Pass 2: D-2 fails again (no progress → stop).
        client.post.side_effect = [
            {
                "results": [
                    {"index": 0, "status": "created", "document_id": "NEW-1"},
                    {"index": 1, "status": "error", "error": "validation failed"},
                ],
                "succeeded": 1,
                "failed": 1,
            },
            {
                "results": [
                    {"index": 0, "status": "error", "error": "validation failed"},
                ],
                "succeeded": 0,
                "failed": 1,
            },
        ]
        remapper = IDRemapper()
        remapper.add_template_mapping("TPL", "NEW-TPL")
        stats = ImportStats(mode="fresh", target_namespace="ns")
        documents = [
            {"document_id": "D-1", "template_id": "TPL", "version": 1, "data": {}},
            {"document_id": "D-2", "template_id": "TPL", "version": 1, "data": {}},
        ]

        _create_documents(client, "ns", documents, remapper, 50, stats, False)

        assert stats.created.documents == 1
        assert stats.failed.documents == 1
        assert remapper.document_map["D-1"] == "NEW-1"
        assert "D-2" not in remapper.document_map
        assert len(stats.errors) >= 1

    def test_create_documents_api_error_continue(self):
        client = MagicMock()
        client.post.side_effect = WIPClientError("timeout", status_code=504)
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        documents = [
            {"document_id": "D-1", "template_id": "TPL", "version": 1, "data": {}},
        ]

        _create_documents(client, "ns", documents, remapper, 50, stats, continue_on_error=True)

        assert stats.failed.documents == 1


# ---------------------------------------------------------------------------
# _upload_files
# ---------------------------------------------------------------------------

class TestUploadFiles:
    def test_upload_files_maps_ids(self):
        client = MagicMock()
        client.post_form.return_value = _ok_file("NEW-FILE-001")
        reader = MagicMock()
        reader.list_blobs.return_value = ["OLD-FILE-001"]
        reader.read_blob.return_value = b"data"
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        files = [
            {
                "file_id": "OLD-FILE-001",
                "filename": "test.pdf",
                "content_type": "application/pdf",
                "metadata": {},
            },
        ]

        _upload_files(client, "ns", files, reader, remapper, stats, False)

        assert remapper.file_map["OLD-FILE-001"] == "NEW-FILE-001"
        assert stats.created.files == 1

    def test_upload_files_missing_blob(self):
        """Files not present in blobs are skipped."""
        client = MagicMock()
        reader = MagicMock()
        reader.list_blobs.return_value = []  # No blobs at all
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        files = [
            {"file_id": "MISSING-FILE", "filename": "missing.txt", "metadata": {}},
        ]

        _upload_files(client, "ns", files, reader, remapper, stats, False)

        client.post_form.assert_not_called()
        assert stats.skipped.files == 1

    def test_upload_files_blob_read_returns_none(self):
        """Blob exists in list but read_blob returns None."""
        client = MagicMock()
        reader = MagicMock()
        reader.list_blobs.return_value = ["FILE-1"]
        reader.read_blob.return_value = None
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        files = [
            {"file_id": "FILE-1", "filename": "empty.txt", "metadata": {}},
        ]

        _upload_files(client, "ns", files, reader, remapper, stats, False)

        client.post_form.assert_not_called()
        assert stats.skipped.files == 1

    def test_upload_files_with_metadata(self):
        """File metadata (description, tags, category) is sent."""
        client = MagicMock()
        client.post_form.return_value = _ok_file("NEW-F")
        reader = MagicMock()
        reader.list_blobs.return_value = ["F-1"]
        reader.read_blob.return_value = b"content"
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        files = [
            {
                "file_id": "F-1",
                "filename": "report.csv",
                "content_type": "text/csv",
                "metadata": {
                    "description": "Monthly report",
                    "tags": ["finance", "monthly"],
                    "category": "reports",
                },
            },
        ]

        _upload_files(client, "ns", files, reader, remapper, stats, False)

        _, kwargs = client.post_form.call_args
        assert kwargs["data"]["description"] == "Monthly report"
        assert kwargs["data"]["tags"] == "finance,monthly"
        assert kwargs["data"]["category"] == "reports"

    def test_upload_files_error_continue(self):
        client = MagicMock()
        client.post_form.side_effect = WIPClientError("upload failed", status_code=500)
        reader = MagicMock()
        reader.list_blobs.return_value = ["F-1"]
        reader.read_blob.return_value = b"data"
        remapper = IDRemapper()
        stats = ImportStats(mode="fresh", target_namespace="ns")
        files = [
            {"file_id": "F-1", "filename": "f.txt", "content_type": "text/plain", "metadata": {}},
        ]

        _upload_files(client, "ns", files, reader, remapper, stats, continue_on_error=True)

        assert stats.failed.files == 1
        assert len(stats.errors) == 1


# ---------------------------------------------------------------------------
# _register_synonyms
# ---------------------------------------------------------------------------

class TestRegisterSynonyms:
    def test_register_synonyms_old_to_new(self):
        """Old-to-new ID pairs from remapper are registered as synonyms."""
        client = MagicMock()
        client.post.return_value = _ok_synonyms(2)
        reader = _make_reader()
        remapper = IDRemapper()
        remapper.add_terminology_mapping("OLD-T", "NEW-T")
        remapper.add_template_mapping("OLD-TPL", "NEW-TPL")
        stats = ImportStats(mode="fresh", target_namespace="ns")

        _register_synonyms(client, "ns", reader, remapper, stats, False)

        call_args = client.post.call_args
        payload = call_args[1]["json"]
        target_ids = {item["target_id"] for item in payload}
        assert "NEW-T" in target_ids
        assert "NEW-TPL" in target_ids
        # Check that old IDs are in composite keys
        composite_keys = [item["synonym_composite_key"] for item in payload]
        original_ids = {ck["original_id"] for ck in composite_keys}
        assert "OLD-T" in original_ids
        assert "OLD-TPL" in original_ids

    def test_register_synonyms_restores_registry_synonyms(self):
        """_registry synonyms from entities are restored with remapped target IDs."""
        client = MagicMock()
        client.post.return_value = _ok_synonyms(3)
        remapper = IDRemapper()
        remapper.add_terminology_mapping("OLD-TERM-1", "NEW-TERM-1")

        # Entity with _registry synonyms
        terminologies = [
            {
                "terminology_id": "OLD-TERM-1",
                "value": "COUNTRY",
                "_registry": {
                    "synonyms": [
                        {
                            "namespace": "wip",
                            "entity_type": "terminologies",
                            "composite_key": {"external_code": "ISO-3166"},
                        },
                    ],
                },
            },
        ]
        reader = _make_reader(terminologies=terminologies)
        stats = ImportStats(mode="fresh", target_namespace="ns")

        _register_synonyms(client, "ns", reader, remapper, stats, False)

        payload = client.post.call_args[1]["json"]
        # Should have 1 old-to-new pair + 1 restored registry synonym
        registry_syns = [
            item for item in payload
            if item.get("synonym_composite_key", {}).get("external_code") == "ISO-3166"
        ]
        assert len(registry_syns) == 1
        assert registry_syns[0]["target_id"] == "NEW-TERM-1"
        assert registry_syns[0]["synonym_entity_type"] == "terminologies"

    def test_register_synonyms_remaps_composite_keys(self):
        """IDs within composite keys of _registry synonyms are remapped."""
        client = MagicMock()
        client.post.return_value = _ok_synonyms(3)
        remapper = IDRemapper()
        remapper.add_document_mapping("OLD-DOC-1", "NEW-DOC-1")
        remapper.add_template_mapping("OLD-TPL-1", "NEW-TPL-1")

        documents = [
            {
                "document_id": "OLD-DOC-1",
                "template_id": "OLD-TPL-1",
                "version": 1,
                "data": {},
                "_registry": {
                    "synonyms": [
                        {
                            "namespace": "wip",
                            "entity_type": "documents",
                            "composite_key": {
                                "template_id": "OLD-TPL-1",
                                "identity_hash": "abc123",
                            },
                        },
                    ],
                },
            },
        ]
        reader = _make_reader(documents=documents)
        stats = ImportStats(mode="fresh", target_namespace="ns")

        _register_synonyms(client, "ns", reader, remapper, stats, False)

        payload = client.post.call_args[1]["json"]
        # Find the restored registry synonym
        restored = [
            item for item in payload
            if "identity_hash" in item.get("synonym_composite_key", {})
        ]
        assert len(restored) == 1
        # template_id within the composite key should be remapped
        assert restored[0]["synonym_composite_key"]["template_id"] == "NEW-TPL-1"
        # identity_hash is a string but not in any map, so stays the same
        assert restored[0]["synonym_composite_key"]["identity_hash"] == "abc123"
        # target_id should be the new document ID
        assert restored[0]["target_id"] == "NEW-DOC-1"

    def test_register_synonyms_no_items(self):
        """No synonyms to register prints message and returns."""
        client = MagicMock()
        reader = _make_reader()
        remapper = IDRemapper()  # No mappings
        stats = ImportStats(mode="fresh", target_namespace="ns")

        _register_synonyms(client, "ns", reader, remapper, stats, False)

        client.post.assert_not_called()
        assert stats.synonyms_registered == 0

    def test_register_synonyms_counts(self):
        """synonyms_registered is set from successful responses."""
        client = MagicMock()
        client.post.return_value = {
            "results": [
                {"status": "added"},
                {"status": "already_exists"},
            ],
        }
        remapper = IDRemapper()
        remapper.add_term_mapping("OLD", "NEW")
        reader = _make_reader()
        stats = ImportStats(mode="fresh", target_namespace="ns")

        _register_synonyms(client, "ns", reader, remapper, stats, False)

        # 1 old-to-new pair, but response has 2 results (both count)
        assert stats.synonyms_registered == 2


# ---------------------------------------------------------------------------
# fresh_import full flow
# ---------------------------------------------------------------------------

class TestFreshImportFullFlow:
    def test_fresh_full_flow(self):
        """End-to-end: all entity types created with new IDs, remapper populated."""
        client = MagicMock()
        # _ensure_namespace: namespace exists
        client.get.return_value = {"prefix": "target-ns"}
        # _create_terminologies: 1 terminology
        # _create_terms: 1 term
        # _create_templates_multipass: 1 template (POST) + 1 activation
        # _create_documents: 1 document
        # _upload_files: 1 file
        client.post.side_effect = [
            # terminology creation
            _ok_terminology("NEW-TERM-001"),
            # term creation
            _ok_terms([(0, "NEW-T-001")]),
            # template creation (POST)
            _ok_template("NEW-TPL-001"),
            # template activation
            {},
            # document creation
            _ok_documents([(0, "NEW-DOC-001")]),
        ]
        client.post_form.return_value = _ok_file("NEW-FILE-001")

        terminologies = [
            {"terminology_id": "OLD-TERM-001", "value": "COUNTRY"},
        ]
        terms = [
            {"term_id": "OLD-T-001", "terminology_id": "OLD-TERM-001", "value": "UK"},
        ]
        templates = [
            {
                "template_id": "OLD-TPL-001",
                "value": "PERSON",
                "version": 1,
                "fields": [
                    {"name": "country", "type": "term", "terminology_ref": "OLD-TERM-001"},
                ],
            },
        ]
        documents = [
            {
                "document_id": "OLD-DOC-001",
                "template_id": "OLD-TPL-001",
                "version": 1,
                "data": {"name": "Alice"},
            },
        ]
        files_meta = [
            {
                "file_id": "OLD-FILE-001",
                "filename": "photo.jpg",
                "content_type": "image/jpeg",
                "metadata": {},
            },
        ]

        reader = _make_reader(
            terminologies=terminologies,
            terms=terms,
            templates=templates,
            documents=documents,
            files=files_meta,
            blobs=["OLD-FILE-001"],
        )

        stats = fresh_import(
            client, reader, "target-ns",
            batch_size=50,
        )

        assert stats.mode == "fresh"
        assert stats.source_namespace == "source-ns"
        assert stats.target_namespace == "target-ns"
        assert stats.created.terminologies == 1
        assert stats.created.terms == 1
        assert stats.created.templates == 1
        assert stats.created.documents == 1
        assert stats.created.files == 1
        assert stats.id_mappings == 5  # 1 terminology + 1 term + 1 template + 1 doc + 1 file

    def test_fresh_dry_run(self):
        """Dry run returns empty stats and makes no API calls."""
        client = MagicMock()
        reader = _make_reader()

        stats = fresh_import(client, reader, "target-ns", dry_run=True)

        assert stats.mode == "fresh"
        assert stats.source_namespace == "source-ns"
        assert stats.created.total == 0
        # No API calls should have been made
        client.get.assert_not_called()
        client.post.assert_not_called()
        client.put.assert_not_called()
        client.post_form.assert_not_called()


# ---------------------------------------------------------------------------
# Skip flags
# ---------------------------------------------------------------------------

class TestSkipFlags:
    def test_skip_documents(self):
        """With skip_documents=True, no document creation occurs. Files still uploaded."""
        client = MagicMock()
        client.get.return_value = {"prefix": "ns"}
        # terminology + term + template POST + activate
        client.post.side_effect = [
            _ok_terminology("NEW-TERM"),
            _ok_terms([(0, "NEW-T")]),
            _ok_template("NEW-TPL"),
            {},  # activate
        ]
        client.post_form.return_value = _ok_file("NEW-FILE")

        reader = _make_reader(
            terminologies=[{"terminology_id": "T1", "value": "V"}],
            terms=[{"term_id": "T-1", "terminology_id": "T1", "value": "v"}],
            templates=[{"template_id": "TPL-1", "value": "X", "version": 1, "fields": []}],
            documents=[{"document_id": "D-1", "template_id": "TPL-1", "version": 1, "data": {}}],
            files=[{"file_id": "F-1", "filename": "f.txt", "content_type": "text/plain", "metadata": {}}],
            blobs=["F-1"],
        )

        stats = fresh_import(client, reader, "ns", skip_documents=True)

        assert stats.created.documents == 0
        assert stats.created.files == 1  # files uploaded independently of documents
        # 4 posts total: terminology, terms, template, activate
        assert client.post.call_count == 4

    def test_skip_files(self):
        """With skip_files=True, no file upload occurs but documents still created."""
        client = MagicMock()
        client.get.return_value = {"prefix": "ns"}
        # terminology + term + template POST + activate + document
        client.post.side_effect = [
            _ok_terminology("NEW-TERM"),
            _ok_terms([(0, "NEW-T")]),
            _ok_template("NEW-TPL"),
            {},  # activate
            _ok_documents([(0, "NEW-DOC")]),
        ]

        reader = _make_reader(
            terminologies=[{"terminology_id": "T1", "value": "V"}],
            terms=[{"term_id": "T-1", "terminology_id": "T1", "value": "v"}],
            templates=[{"template_id": "TPL-1", "value": "X", "version": 1, "fields": []}],
            documents=[{"document_id": "D-1", "template_id": "TPL-1", "version": 1, "data": {}}],
            files=[{"file_id": "F-1", "filename": "f.txt", "metadata": {}}],
            blobs=["F-1"],
        )

        stats = fresh_import(client, reader, "ns", skip_files=True)

        assert stats.created.documents == 1
        assert stats.created.files == 0
        client.post_form.assert_not_called()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestContinueOnError:
    def test_continue_on_error(self):
        """Failures are recorded but do not raise when continue_on_error=True."""
        client = MagicMock()
        client.get.return_value = {"prefix": "ns"}
        # Terminology fails, but continue
        client.post.side_effect = [
            # Terminology: error
            {
                "results": [{"index": 0, "status": "error", "error": "bad terminology"}],
                "succeeded": 0,
                "failed": 1,
            },
            # Terms: no mappings for the terminology, so they'll be skipped
            # Templates: error
            {
                "results": [{"index": 0, "status": "error", "error": "bad template"}],
                "succeeded": 0,
                "failed": 1,
            },
            # Documents: succeed
            _ok_documents([(0, "NEW-DOC")]),
        ]

        reader = _make_reader(
            terminologies=[{"terminology_id": "T1", "value": "V"}],
            terms=[{"term_id": "TM-1", "terminology_id": "T1", "value": "v"}],
            templates=[{"template_id": "TPL-1", "value": "X", "version": 1, "fields": []}],
            documents=[{"document_id": "D-1", "template_id": "TPL-1", "version": 1, "data": {}}],
        )

        stats = fresh_import(
            client, reader, "ns",
            continue_on_error=True,
        )

        assert stats.failed.terminologies == 1
        assert stats.skipped.terms == 1  # Terminology not mapped
        assert stats.failed.templates == 1
        assert stats.created.documents == 1
        assert len(stats.errors) >= 2

    def test_register_synonyms_flow(self):
        """Full flow with register_synonyms=True calls _register_synonyms."""
        client = MagicMock()
        client.get.return_value = {"prefix": "ns"}
        client.post.side_effect = [
            _ok_terminology("NEW-TERM"),
            _ok_terms([(0, "NEW-T")]),
            _ok_template("NEW-TPL"),
            {},  # activate
            _ok_documents([(0, "NEW-DOC")]),
            # synonym registration
            _ok_synonyms(4),
        ]

        reader = _make_reader(
            terminologies=[{"terminology_id": "T1", "value": "V"}],
            terms=[{"term_id": "TM-1", "terminology_id": "T1", "value": "v"}],
            templates=[{"template_id": "TPL-1", "value": "X", "version": 1, "fields": []}],
            documents=[{"document_id": "D-1", "template_id": "TPL-1", "version": 1, "data": {}}],
        )

        stats = fresh_import(
            client, reader, "ns",
            register_synonyms=True,
        )

        assert stats.synonyms_registered == 4
