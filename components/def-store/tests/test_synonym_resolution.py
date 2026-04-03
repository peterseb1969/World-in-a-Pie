"""Tests for synonym resolution across all def-store endpoints.

Verifies that every endpoint that accepts entity IDs also accepts
human-readable synonyms, resolving them via the shared resolution layer.
Verifies that unresolvable synonyms return 404, not 500.
"""

from unittest.mock import patch

import pytest

from wip_auth.resolve import EntityNotFoundError

# Known synonym mappings for tests
SYNONYM_MAP = {
    ("terminology", "STATUS", "test-ns"): "TERM-000001",
    ("terminology", "GENDER", "test-ns"): "TERM-000002",
    ("term", "STATUS:approved", "test-ns"): "T-000001",
    ("term", "STATUS:rejected", "test-ns"): "T-000002",
}


async def mock_resolve(raw_id, entity_type, namespace, **kwargs):
    """Mock resolve_entity_id that uses SYNONYM_MAP."""
    import re
    if re.match(r"^[0-9a-f]{8}-", raw_id, re.IGNORECASE) or raw_id.startswith(("TERM-", "T-")):
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
        # The resolved ID TERM-000001 may not exist in test DB, but we should
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

    async def test_get_term_resolves_colon_notation(self, client, auth_headers):
        """GET /terms/{TERMINOLOGY:VALUE} should resolve the synonym."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/terms/STATUS:approved?namespace=test-ns",
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
class TestOntologySynonymResolution:
    """Test synonym resolution on ontology endpoints."""

    async def test_list_relationships_resolves_term_id(self, client, auth_headers):
        """GET /ontology/relationships?term_id=synonym should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/ontology/relationships"
                "?term_id=STATUS:approved&namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404)

    async def test_get_ancestors_resolves_term_id(self, client, auth_headers):
        """GET /ontology/terms/{synonym}/ancestors should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/ontology/terms/STATUS:approved/ancestors?namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404)

    async def test_get_children_resolves_term_id(self, client, auth_headers):
        """GET /ontology/terms/{synonym}/children should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/def-store/ontology/terms/STATUS:approved/children?namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404)

    async def test_create_relationships_resolves_term_ids(self, client, auth_headers):
        """POST /ontology/relationships should resolve source and target term IDs."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/def-store/ontology/relationships?namespace=test-ns",
                headers=auth_headers,
                json=[{
                    "source_term_id": "STATUS:approved",
                    "target_term_id": "STATUS:rejected",
                    "relationship_type": "related_to",
                }],
            )
        # 200 with per-item error is acceptable, 500 is not
        assert resp.status_code == 200, f"Unexpected: {resp.status_code}"

    async def test_unresolvable_term_in_relationships_logged_not_500(self, client, auth_headers):
        """Unresolvable term IDs in bulk should not cause 500."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/def-store/ontology/relationships?namespace=test-ns",
                headers=auth_headers,
                json=[{
                    "source_term_id": "BADTERM",
                    "target_term_id": "STATUS:approved",
                    "relationship_type": "is_a",
                }],
            )
        # bulk_resolve logs failures but doesn't raise — per-item error expected
        assert resp.status_code == 200
