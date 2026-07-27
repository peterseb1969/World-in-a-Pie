"""Tests for synonym resolution across all def-store endpoints.

Verifies that every endpoint that accepts entity IDs also accepts
human-readable synonyms, resolving them via the shared resolution layer.
Verifies that unresolvable synonyms return 404, not 500.

Term identifiers are the exception to "any synonym form works": the
2-part 'TERMINOLOGY:VALUE' shorthand is ambiguous (a value containing
':' is indistinguishable from it) and every term door rejects it with
422. Term value addressing uses the fully qualified
'ns:terminology:value' form or the terminology-scoped field form.
"""

from unittest.mock import patch

import pytest

from wip_auth.resolve import EntityNotFoundError

# Known synonym mappings for tests
SYNONYM_MAP = {
    ("terminology", "STATUS", "test-ns"): "0190a000-0000-7000-0000-000000000001",
    ("terminology", "GENDER", "test-ns"): "0190a000-0000-7000-0000-000000000002",
    # Terms resolve by the fully qualified 3-part form only — the 2-part
    # shorthand is rejected at the door and never reaches resolution.
    ("term", "test-ns:STATUS:approved", "test-ns"): "0190b000-0000-7000-0000-000000000001",
    ("term", "test-ns:STATUS:rejected", "test-ns"): "0190b000-0000-7000-0000-000000000002",
    # Also support namespace=None for endpoints without namespace param
    ("terminology", "STATUS", None): "0190a000-0000-7000-0000-000000000001",
    ("terminology", "GENDER", None): "0190a000-0000-7000-0000-000000000002",
    ("term", "test-ns:STATUS:approved", None): "0190b000-0000-7000-0000-000000000001",
    ("term", "test-ns:STATUS:rejected", None): "0190b000-0000-7000-0000-000000000002",
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
class TestTermsSynonymResolution:
    """Test synonym resolution on term endpoints."""

    async def test_create_terms_resolves_terminology_synonym(self, client, auth_headers):
        """POST /terminologies/{synonym}/terms should resolve the synonym."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/def-store/terminologies/STATUS/terms?namespace=test-ns",
                headers=auth_headers,
                json=[{"value": "new_term", "label": "New Term"}],
            )
        # The resolved ID 0190a000-0000-7000-0000-000000000001 may not exist in test DB, but we should
        # NOT get a 500 — we should get 404 (terminology not found in DB) or 200
        assert resp.status_code in (200, 404), f"Unexpected status: {resp.status_code} {resp.text}"

    async def test_create_terms_unresolvable_returns_404(self, client, auth_headers):
        """POST /terminologies/{bad_synonym}/terms should return 404."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/def-store/terminologies/NONEXISTENT/terms?namespace=test-ns",
                headers=auth_headers,
                json=[{"value": "x", "label": "X"}],
            )
        assert resp.status_code == 404
        assert "resolve" in resp.json().get("detail", "").lower() or "not found" in resp.json().get("detail", "").lower()

    async def test_list_terms_resolves_terminology_synonym(self, client, auth_headers):
        """GET /terminologies/{synonym}/terms should resolve the synonym."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/terminologies/STATUS/terms?namespace=test-ns",
                headers=auth_headers,
            )
        # 404 is acceptable (no data), 500 is not
        assert resp.status_code in (200, 404), f"Unexpected: {resp.status_code}"

    async def test_get_term_rejects_two_part_colon_notation(self, client, auth_headers):
        """GET /terms/{TERMINOLOGY:VALUE} rejects the ambiguous 2-part form."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/terms/STATUS:approved?namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code == 422, f"Unexpected: {resp.status_code}"
        assert "terminology=" in resp.json()["detail"]

    async def test_get_term_resolves_qualified_form(self, client, auth_headers):
        """GET /terms/{ns:terminology:value} resolves the lossless 3-part form."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/terms/test-ns:STATUS:approved?namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404), f"Unexpected: {resp.status_code}"

    async def test_get_term_unresolvable_returns_404(self, client, auth_headers):
        """GET /terms/{bad_synonym} should return 404, not 500."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/terms/DOESNOTEXIST?namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    async def test_deprecate_rejects_two_part_replaced_by_term_id(self, client, auth_headers):
        """POST /terms/deprecate rejects a 2-part replaced_by_term_id — the
        pointer is persisted, so an ambiguous form is a wrong-hit risk."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/def-store/terms/deprecate",
                headers=auth_headers,
                json=[{
                    "term_id": "0190b000-0000-7000-0000-000000000001",
                    "reason": "Replaced by rejected",
                    "replaced_by_term_id": "STATUS:rejected",
                }],
            )
        assert resp.status_code == 422, f"Unexpected: {resp.status_code}"

    async def test_deprecate_accepts_qualified_replaced_by_term_id(self, client, auth_headers):
        """POST /terms/deprecate accepts the lossless 3-part form."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/def-store/terms/deprecate",
                headers=auth_headers,
                json=[{
                    "term_id": "0190b000-0000-7000-0000-000000000001",
                    "reason": "Replaced by rejected",
                    "replaced_by_term_id": "test-ns:STATUS:rejected",
                }],
            )
        # 200 with per-item result (term may not exist in test DB)
        assert resp.status_code == 200, f"Unexpected: {resp.status_code}"


@pytest.mark.asyncio
class TestTerminologySynonymResolution:
    """Test synonym resolution on terminology endpoints."""

    async def test_get_terminology_resolves_synonym(self, client, auth_headers):
        """GET /terminologies/{synonym} should resolve via Registry."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/terminologies/STATUS?namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404)

    async def test_get_terminology_unresolvable_returns_404(self, client, auth_headers):
        """GET /terminologies/{bad} should return 404."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/terminologies/NOPE?namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestAuditSynonymResolution:
    """Test synonym resolution on audit endpoints."""

    async def test_term_audit_rejects_two_part_form(self, client, auth_headers):
        """GET /audit/terms/{TERMINOLOGY:VALUE} rejects the ambiguous form —
        a wrong-hit here silently returns the wrong term's history."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/audit/terms/STATUS:approved",
                headers=auth_headers,
            )
        assert resp.status_code == 422, f"Unexpected: {resp.status_code}"

    async def test_term_audit_resolves_qualified_form(self, client, auth_headers):
        """GET /audit/terms/{ns:terminology:value} resolves the 3-part form."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/audit/terms/test-ns:STATUS:approved",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404), f"Unexpected: {resp.status_code}"

    async def test_term_audit_unresolvable_returns_empty(self, client, auth_headers):
        """GET /audit/terms/{bad} returns empty results (no namespace = passthrough)."""
        # Without namespace context, resolve_or_404 passes raw ID through.
        # MongoDB finds nothing, so we get 200 with empty items.
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/audit/terms/DOESNOTEXIST",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    async def test_terminology_audit_resolves_synonym(self, client, auth_headers):
        """GET /audit/terminologies/{synonym} should resolve via Registry."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/audit/terminologies/STATUS",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404), f"Unexpected: {resp.status_code}"


@pytest.mark.asyncio
class TestExportSynonymResolution:
    """Test synonym resolution on export endpoint."""

    async def test_export_resolves_terminology_synonym(self, client, auth_headers):
        """GET /import-export/export/{synonym} should resolve via Registry."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/import-export/export/STATUS",
                headers=auth_headers,
            )
        # 404 is acceptable (no data in test DB), 500 is not
        assert resp.status_code in (200, 404), f"Unexpected: {resp.status_code}"

    async def test_export_unresolvable_returns_404(self, client, auth_headers):
        """GET /import-export/export/{bad} should return 404."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/import-export/export/NONEXISTENT",
                headers=auth_headers,
            )
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestOntologySynonymResolution:
    """Test synonym resolution on ontology endpoints."""

    async def test_list_relations_rejects_two_part_term_id(self, client, auth_headers):
        """GET /ontology/term-relations?term_id=TERMINOLOGY:VALUE is rejected."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/ontology/term-relations"
                "?term_id=STATUS:approved&namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code == 422

    async def test_list_relations_resolves_qualified_term_id(self, client, auth_headers):
        """GET /ontology/term-relations?term_id={3-part} resolves."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/ontology/term-relations"
                "?term_id=test-ns:STATUS:approved&namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404)

    async def test_get_ancestors_rejects_two_part_term_id(self, client, auth_headers):
        """GET /ontology/terms/{TERMINOLOGY:VALUE}/ancestors is rejected."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/ontology/terms/STATUS:approved/ancestors?namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code == 422

    async def test_get_children_resolves_qualified_term_id(self, client, auth_headers):
        """GET /ontology/terms/{3-part}/children resolves."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/ontology/terms/test-ns:STATUS:approved/children?namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404)

    async def test_create_relations_rejects_two_part_term_ids(self, client, auth_headers):
        """POST /ontology/term-relations rejects 2-part endpoint ids — a
        wrong-hit here creates an edge between the wrong terms."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/def-store/ontology/term-relations?namespace=test-ns",
                headers=auth_headers,
                json=[{
                    "source_term_id": "STATUS:approved",
                    "target_term_id": "STATUS:rejected",
                    "relation_type": "related_to",
                }],
            )
        assert resp.status_code == 422, f"Unexpected: {resp.status_code}"

    async def test_create_relations_resolves_qualified_term_ids(self, client, auth_headers):
        """POST /ontology/term-relations resolves the 3-part form."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/def-store/ontology/term-relations?namespace=test-ns",
                headers=auth_headers,
                json=[{
                    "source_term_id": "test-ns:STATUS:approved",
                    "target_term_id": "test-ns:STATUS:rejected",
                    "relation_type": "related_to",
                }],
            )
        # 200 with per-item error is acceptable, 500 is not
        assert resp.status_code == 200, f"Unexpected: {resp.status_code}"

    async def test_unresolvable_term_in_relations_logged_not_500(self, client, auth_headers):
        """Unresolvable term IDs in bulk should not cause 500."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/def-store/ontology/term-relations?namespace=test-ns",
                headers=auth_headers,
                json=[{
                    "source_term_id": "BADTERM",
                    "target_term_id": "test-ns:STATUS:approved",
                    "relation_type": "is_a",
                }],
            )
        # bulk_resolve logs failures but doesn't raise — per-item error expected
        assert resp.status_code == 200
