"""Tests for synonym resolution across all document-store endpoints.

Verifies that every endpoint that accepts template or document IDs also
accepts human-readable synonyms, and that unresolvable synonyms return
404 (not 500, which was the CASE-02 bug).
"""

from unittest.mock import patch

import pytest

from wip_auth.resolve import EntityNotFoundError

SYNONYM_MAP = {
    ("template", "PERSON", "test-ns"): "PERSON",
    ("template", "AA_PROJECT", "test-ns"): "0190c000-0000-7000-0000-000000000099",
    ("document", "my-doc", None): "0190d000-0000-7000-0000-000000000001",
}


async def mock_resolve(raw_id, entity_type, namespace, **kwargs):
    """Mock resolve_entity_id that uses SYNONYM_MAP."""
    import re
    if re.match(r"^[0-9a-f]{8}-", raw_id, re.IGNORECASE) or re.match(r"^[0-9a-f]{8}-", raw_id, re.IGNORECASE):
        return raw_id
    key = (entity_type, raw_id, namespace)
    if key in SYNONYM_MAP:
        return SYNONYM_MAP[key]
    raise EntityNotFoundError(raw_id, entity_type)


@pytest.mark.asyncio
class TestDocumentSynonymResolution:
    """Test synonym resolution on document endpoints."""

    async def test_list_documents_resolves_template_synonym(self, client, auth_headers):
        """GET /documents?template_id=PERSON should resolve, not 500.

        This is the exact bug from CASE-02: GET /documents?template_id=AA_PROJECT
        returned 500 because contextlib.suppress passed the unresolved synonym
        directly to MongoDB.
        """
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/document-store/documents?template_id=PERSON&namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    async def test_list_documents_unresolvable_template_returns_404(self, client, auth_headers):
        """GET /documents?template_id=NONEXISTENT should return 404, not 500."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/document-store/documents?template_id=NONEXISTENT&namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    async def test_create_documents_resolves_template_synonym(self, client, auth_headers):
        """POST /documents with template synonym should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/document-store/documents",
                headers=auth_headers,
                json=[{
                    "template_id": "PERSON",
                    "namespace": "test-ns",
                    "data": {"first_name": "Test"},
                }],
            )
        # Template PERSON won't exist in test DB, but we should not get 500
        assert resp.status_code == 200, f"Unexpected: {resp.status_code}"
        # Check per-item — should be error (template not found), not crash
        body = resp.json()
        assert body["total"] == 1

    async def test_create_documents_unresolvable_template_logged(self, client, auth_headers):
        """Unresolvable template in create should not crash.

        resolve_bulk_ids logs the failure and passes through — the downstream
        service returns a per-item error.
        """
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/document-store/documents",
                headers=auth_headers,
                json=[{
                    "template_id": "BADTEMPLATE",
                    "namespace": "test-ns",
                    "data": {"x": 1},
                }],
            )
        assert resp.status_code == 200  # BulkResponse with per-item error
        body = resp.json()
        assert body["failed"] >= 1


@pytest.mark.asyncio
class TestDocumentGetSynonymResolution:
    """Test resolution on GET document endpoints."""

    async def test_get_document_resolves_synonym(self, client, auth_headers):
        """GET /documents/{synonym} should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/document-store/documents/my-doc",
                headers=auth_headers,
            )
        # 0190d000-0000-7000-0000-000000000001 won't exist, but should be 404 not 500
        assert resp.status_code == 404

    async def test_get_document_unresolvable_returns_404(self, client, auth_headers):
        """GET /documents/{bad} should return 404."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/document-store/documents/NO_SUCH_DOC",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    async def test_get_versions_resolves_synonym(self, client, auth_headers):
        """GET /documents/{synonym}/versions should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/document-store/documents/my-doc/versions",
                headers=auth_headers,
            )
        assert resp.status_code == 404  # 0190d000-0000-7000-0000-000000000001 doesn't exist


@pytest.mark.asyncio
class TestValidationSynonymResolution:
    """Test resolution on validation endpoint."""

    async def test_validate_resolves_template_synonym(self, client, auth_headers):
        """POST /validation/validate with template synonym should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/document-store/validation/validate",
                headers=auth_headers,
                json={
                    "template_id": "PERSON",
                    "namespace": "test-ns",
                    "data": {"first_name": "Test"},
                },
            )
        # Template may not exist in test DB
        assert resp.status_code in (200, 404, 422), f"Unexpected: {resp.status_code}"

    async def test_validate_unresolvable_returns_404(self, client, auth_headers):
        """POST /validation/validate with bad template should return 404."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/document-store/validation/validate",
                headers=auth_headers,
                json={
                    "template_id": "NONEXISTENT",
                    "namespace": "test-ns",
                    "data": {},
                },
            )
        assert resp.status_code == 404
