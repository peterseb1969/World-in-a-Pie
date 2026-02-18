"""Tests for EntityCollector using mock WIPClient."""

from unittest.mock import MagicMock, call, patch

import pytest

from wip_toolkit.export.collector import EntityCollector


@pytest.fixture
def mock_client():
    """Create a mock WIPClient."""
    client = MagicMock()
    client.fetch_all_paginated = MagicMock()
    client.get = MagicMock()
    client.post = MagicMock()
    client.get_stream = MagicMock()
    return client


@pytest.fixture
def collector(mock_client):
    """Create an EntityCollector with mock client."""
    return EntityCollector(mock_client, namespace="wip", include_inactive=False)


@pytest.fixture
def collector_inactive(mock_client):
    """Create an EntityCollector that includes inactive entities."""
    return EntityCollector(mock_client, namespace="wip", include_inactive=True)


class TestFetchTerminologies:
    """Test terminology fetching."""

    def test_returns_paginated_results(self, collector, mock_client, sample_terminologies):
        mock_client.fetch_all_paginated.return_value = sample_terminologies

        result = collector.fetch_terminologies()

        assert len(result) == 2
        assert result[0]["terminology_id"] == "TERM-000001"
        assert result[1]["terminology_id"] == "TERM-000002"

    def test_passes_namespace_param(self, collector, mock_client):
        mock_client.fetch_all_paginated.return_value = []

        collector.fetch_terminologies()

        call_args = mock_client.fetch_all_paginated.call_args
        assert call_args[1]["params"]["namespace"] == "wip"

    def test_filters_active_by_default(self, collector, mock_client):
        mock_client.fetch_all_paginated.return_value = []

        collector.fetch_terminologies()

        call_args = mock_client.fetch_all_paginated.call_args
        assert call_args[1]["params"]["status"] == "active"

    def test_include_inactive_skips_status_filter(self, collector_inactive, mock_client):
        mock_client.fetch_all_paginated.return_value = []

        collector_inactive.fetch_terminologies()

        call_args = mock_client.fetch_all_paginated.call_args
        assert "status" not in call_args[1]["params"]

    def test_uses_def_store_service(self, collector, mock_client):
        mock_client.fetch_all_paginated.return_value = []

        collector.fetch_terminologies()

        call_args = mock_client.fetch_all_paginated.call_args
        assert call_args[0][0] == "def-store"
        assert call_args[0][1] == "/terminologies"

    def test_empty_result(self, collector, mock_client):
        mock_client.fetch_all_paginated.return_value = []

        result = collector.fetch_terminologies()
        assert result == []


class TestFetchTerms:
    """Test term fetching per terminology."""

    def test_fetch_terms_for_terminology(self, collector, mock_client, sample_terms):
        mock_client.fetch_all_paginated.return_value = sample_terms[:2]

        result = collector.fetch_terms("TERM-000001")

        assert len(result) == 2
        call_args = mock_client.fetch_all_paginated.call_args
        assert "/terminologies/TERM-000001/terms" in call_args[0][1]

    def test_fetch_all_terms(self, collector, mock_client, sample_terminologies, sample_terms):
        # First call for TERM-000001 returns 2 terms, second for TERM-000002 returns 1
        mock_client.fetch_all_paginated.side_effect = [
            sample_terms[:2],  # UK, France
            sample_terms[2:],  # active
        ]

        result = collector.fetch_all_terms(sample_terminologies)

        assert len(result) == 3
        assert mock_client.fetch_all_paginated.call_count == 2


class TestFetchTemplates:
    """Test template fetching."""

    def test_returns_all_versions(self, collector, mock_client, sample_templates):
        mock_client.fetch_all_paginated.return_value = sample_templates

        result = collector.fetch_templates()

        assert len(result) == 3
        call_args = mock_client.fetch_all_paginated.call_args
        assert call_args[1]["params"]["latest_only"] == "false"

    def test_passes_namespace(self, collector, mock_client):
        mock_client.fetch_all_paginated.return_value = []

        collector.fetch_templates()

        call_args = mock_client.fetch_all_paginated.call_args
        assert call_args[1]["params"]["namespace"] == "wip"

    def test_filters_active_by_default(self, collector, mock_client):
        mock_client.fetch_all_paginated.return_value = []

        collector.fetch_templates()

        call_args = mock_client.fetch_all_paginated.call_args
        assert call_args[1]["params"]["status"] == "active"

    def test_uses_template_store(self, collector, mock_client):
        mock_client.fetch_all_paginated.return_value = []

        collector.fetch_templates()

        call_args = mock_client.fetch_all_paginated.call_args
        assert call_args[0][0] == "template-store"
        assert call_args[0][1] == "/templates"


class TestFetchDocuments:
    """Test document fetching with deduplication."""

    def test_returns_documents(self, collector, mock_client, sample_documents):
        mock_client.fetch_all_paginated.return_value = sample_documents

        result = collector.fetch_documents()

        assert len(result) == 2

    def test_deduplicates_by_id_and_version(self, collector, mock_client):
        """Duplicate documents across page boundaries are removed."""
        docs = [
            {"document_id": "DOC-1", "version": 1, "data": {"a": 1}},
            {"document_id": "DOC-2", "version": 1, "data": {"b": 2}},
            {"document_id": "DOC-1", "version": 1, "data": {"a": 1}},  # duplicate
            {"document_id": "DOC-2", "version": 1, "data": {"b": 2}},  # duplicate
        ]
        mock_client.fetch_all_paginated.return_value = docs

        result = collector.fetch_documents()

        assert len(result) == 2
        doc_ids = [d["document_id"] for d in result]
        assert "DOC-1" in doc_ids
        assert "DOC-2" in doc_ids

    def test_different_versions_not_deduped(self, collector, mock_client):
        """Same document_id with different versions are kept."""
        docs = [
            {"document_id": "DOC-1", "version": 1, "data": {}},
            {"document_id": "DOC-1", "version": 2, "data": {}},
        ]
        mock_client.fetch_all_paginated.return_value = docs

        result = collector.fetch_documents()

        assert len(result) == 2

    def test_default_version_1(self, collector, mock_client):
        """Documents without version field default to version 1 for dedup."""
        docs = [
            {"document_id": "DOC-1", "data": {}},
            {"document_id": "DOC-1", "data": {}},  # duplicate (both default v1)
        ]
        mock_client.fetch_all_paginated.return_value = docs

        result = collector.fetch_documents()

        assert len(result) == 1

    def test_filters_active_by_default(self, collector, mock_client):
        mock_client.fetch_all_paginated.return_value = []

        collector.fetch_documents()

        call_args = mock_client.fetch_all_paginated.call_args
        assert call_args[1]["params"]["status"] == "active"


class TestFetchRegistryEntries:
    """Test bulk Registry entry fetching."""

    def test_returns_mapped_entries(self, collector, mock_client):
        mock_client.post.return_value = {
            "results": [
                {
                    "status": "found",
                    "entry_id": "TERM-000001",
                    "namespace": "wip",
                    "entity_type": "terminologies",
                    "matched_composite_key": {"value": "COUNTRY"},
                    "synonyms": [],
                    "source_info": None,
                },
            ],
        }

        result = collector.fetch_registry_entries(["TERM-000001"])

        assert "TERM-000001" in result
        assert result["TERM-000001"]["entry_id"] == "TERM-000001"
        assert result["TERM-000001"]["entity_type"] == "terminologies"
        assert result["TERM-000001"]["primary_composite_key"] == {"value": "COUNTRY"}

    def test_batch_processing(self, collector, mock_client):
        """IDs are batched in groups of 100."""
        ids = [f"T-{i:06d}" for i in range(250)]

        # Return empty results for all batches
        mock_client.post.return_value = {"results": []}

        collector.fetch_registry_entries(ids)

        # Should be 3 batches: 100 + 100 + 50
        assert mock_client.post.call_count == 3

    def test_skips_not_found_entries(self, collector, mock_client):
        mock_client.post.return_value = {
            "results": [
                {"status": "not_found", "entry_id": "TERM-MISSING"},
                {
                    "status": "found",
                    "entry_id": "TERM-001",
                    "namespace": "wip",
                    "entity_type": "terminologies",
                    "matched_composite_key": {},
                    "synonyms": [],
                },
            ],
        }

        result = collector.fetch_registry_entries(["TERM-MISSING", "TERM-001"])

        assert "TERM-MISSING" not in result
        assert "TERM-001" in result

    def test_extracts_synonyms(self, collector, mock_client):
        mock_client.post.return_value = {
            "results": [
                {
                    "status": "found",
                    "entry_id": "TERM-001",
                    "namespace": "wip",
                    "entity_type": "terminologies",
                    "matched_composite_key": {"value": "COUNTRY"},
                    "synonyms": [
                        {
                            "namespace": "wip",
                            "entity_type": "terminologies",
                            "composite_key": {"external_code": "ISO-3166"},
                        },
                        {
                            "namespace": "other",
                            "entity_type": "terminologies",
                            "composite_key": {"vendor_id": "V-001"},
                        },
                    ],
                },
            ],
        }

        result = collector.fetch_registry_entries(["TERM-001"])

        synonyms = result["TERM-001"]["synonyms"]
        assert len(synonyms) == 2
        assert synonyms[0]["composite_key"] == {"external_code": "ISO-3166"}
        assert synonyms[1]["namespace"] == "other"

    def test_empty_ids_returns_empty(self, collector, mock_client):
        result = collector.fetch_registry_entries([])
        assert result == {}
        mock_client.post.assert_not_called()

    def test_handles_api_error_gracefully(self, collector, mock_client):
        """API errors for a batch are caught and don't crash the whole operation."""
        mock_client.post.side_effect = Exception("Connection refused")

        result = collector.fetch_registry_entries(["TERM-001"])

        # Should return empty dict, not raise
        assert result == {}


class TestTemplateCaching:
    """Test template cache across multiple calls."""

    def test_cache_populated_on_first_call(self, collector, mock_client, sample_templates):
        mock_client.fetch_all_paginated.return_value = sample_templates

        # First call populates cache
        result = collector.fetch_template_by_id("TPL-000001")

        assert len(result) == 1
        assert result[0]["template_id"] == "TPL-000001"

    def test_cache_reused_on_second_call(self, collector, mock_client, sample_templates):
        mock_client.fetch_all_paginated.return_value = sample_templates

        # First call
        collector.fetch_template_by_id("TPL-000001")
        # Second call should reuse cache
        collector.fetch_template_by_id("TPL-000002")

        # fetch_all_paginated should only be called once for the cache
        assert mock_client.fetch_all_paginated.call_count == 1

    def test_fetch_template_by_id_returns_matches(self, collector, mock_client, sample_templates):
        mock_client.fetch_all_paginated.return_value = sample_templates

        result = collector.fetch_template_by_id("TPL-000002")

        assert len(result) == 1
        assert result[0]["value"] == "EMPLOYEE"

    def test_fetch_template_by_id_not_found(self, collector, mock_client, sample_templates):
        mock_client.fetch_all_paginated.return_value = sample_templates

        result = collector.fetch_template_by_id("TPL-NONEXISTENT")

        assert result == []

    def test_fetch_template_versions_by_id(self, collector, mock_client):
        """Multiple versions of same template_id are returned."""
        templates = [
            {"template_id": "TPL-001", "version": 1, "value": "A"},
            {"template_id": "TPL-001", "version": 2, "value": "A"},
            {"template_id": "TPL-002", "version": 1, "value": "B"},
        ]
        mock_client.fetch_all_paginated.return_value = templates

        result = collector.fetch_template_versions_by_id("TPL-001")

        assert len(result) == 2
        assert all(t["template_id"] == "TPL-001" for t in result)

    def test_cache_handles_api_error(self, collector, mock_client):
        """If API fails, cache is set to empty list."""
        mock_client.fetch_all_paginated.side_effect = Exception("timeout")

        result = collector.fetch_template_by_id("TPL-001")

        assert result == []
        # Cache should be set (empty list), not None
        assert collector._template_cache == []


class TestFetchDocumentVersions:
    """Test document version fetching."""

    def test_returns_version_list(self, collector, mock_client):
        mock_client.get.return_value = {
            "versions": [
                {"version": 1, "created_at": "2024-01-01"},
                {"version": 2, "created_at": "2024-06-01"},
            ],
        }

        result = collector.fetch_document_versions("DOC-001")

        assert len(result) == 2
        mock_client.get.assert_called_once_with(
            "document-store", "/documents/DOC-001/versions",
        )

    def test_fetch_document_version(self, collector, mock_client):
        expected_doc = {"document_id": "DOC-001", "version": 2, "data": {"x": 1}}
        mock_client.get.return_value = expected_doc

        result = collector.fetch_document_version("DOC-001", 2)

        assert result == expected_doc
        mock_client.get.assert_called_once_with(
            "document-store", "/documents/DOC-001/versions/2",
        )


class TestFetchFiles:
    """Test file metadata and content fetching."""

    def test_fetch_files(self, collector, mock_client):
        files = [
            {"file_id": "FILE-001", "filename": "test.pdf"},
            {"file_id": "FILE-002", "filename": "image.png"},
        ]
        mock_client.fetch_all_paginated.return_value = files

        result = collector.fetch_files()

        assert len(result) == 2
        call_args = mock_client.fetch_all_paginated.call_args
        assert call_args[0][0] == "document-store"
        assert call_args[0][1] == "/files"

    def test_fetch_file_content(self, collector, mock_client):
        mock_resp = MagicMock()
        mock_resp.content = b"\x89PNG binary data"
        mock_client.get_stream.return_value = mock_resp

        result = collector.fetch_file_content("FILE-001")

        assert result == b"\x89PNG binary data"
        mock_client.get_stream.assert_called_once_with(
            "document-store", "/files/FILE-001/content",
        )


class TestFetchNamespaceConfig:
    """Test namespace config fetching."""

    def test_returns_config(self, collector, mock_client):
        ns_data = {
            "prefix": "wip",
            "description": "Main namespace",
            "isolation_mode": "open",
        }
        mock_client.get.return_value = ns_data

        result = collector.fetch_namespace_config("wip")

        assert result == ns_data
        mock_client.get.assert_called_once_with("registry", "/namespaces/wip")

    def test_returns_none_on_error(self, collector, mock_client):
        mock_client.get.side_effect = Exception("Not found")

        result = collector.fetch_namespace_config("nonexistent")

        assert result is None


class TestFetchTerminologyById:
    """Test single terminology lookup."""

    def test_returns_terminology(self, collector, mock_client):
        term_data = {"terminology_id": "TERM-001", "value": "COUNTRY"}
        mock_client.get.return_value = term_data

        result = collector.fetch_terminology_by_id("TERM-001")

        assert result == term_data

    def test_returns_none_on_error(self, collector, mock_client):
        mock_client.get.side_effect = Exception("Not found")

        result = collector.fetch_terminology_by_id("TERM-MISSING")

        assert result is None


class TestFetchAllDocumentVersions:
    """Test expansion from latest-only to all versions."""

    def test_expands_to_all_versions(self, collector, mock_client):
        """Expands a single latest-version doc to all its versions."""
        latest_docs = [
            {"document_id": "DOC-1", "version": 2, "data": {"v": 2}},
        ]

        mock_client.get.side_effect = [
            # fetch_document_versions returns version list
            {"versions": [{"version": 1}, {"version": 2}]},
            # fetch_document_version for v1
            {"document_id": "DOC-1", "version": 1, "data": {"v": 1}},
            # fetch_document_version for v2
            {"document_id": "DOC-1", "version": 2, "data": {"v": 2}},
        ]

        result = collector.fetch_all_document_versions(latest_docs)

        assert len(result) == 2
        versions = sorted([d["version"] for d in result])
        assert versions == [1, 2]

    def test_deduplicates_across_input_documents(self, collector, mock_client):
        """Same document_id appearing multiple times in input is deduplicated."""
        docs = [
            {"document_id": "DOC-1", "version": 1, "data": {}},
            {"document_id": "DOC-1", "version": 1, "data": {}},  # dup
        ]

        mock_client.get.side_effect = [
            {"versions": [{"version": 1}]},
            {"document_id": "DOC-1", "version": 1, "data": {}},
        ]

        result = collector.fetch_all_document_versions(docs)

        assert len(result) == 1

    def test_handles_version_fetch_error(self, collector, mock_client):
        """If version listing fails, original documents are preserved."""
        docs = [
            {"document_id": "DOC-1", "version": 1, "data": {"original": True}},
        ]

        mock_client.get.side_effect = Exception("timeout")

        result = collector.fetch_all_document_versions(docs)

        # Should fall back to original doc
        assert len(result) == 1
        assert result[0]["data"]["original"] is True
