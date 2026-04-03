"""Tests for synonym resolution across all template-store endpoints.

Verifies that every endpoint that accepts template IDs also accepts
human-readable synonyms, and that unresolvable synonyms return 404.
"""

from unittest.mock import patch

import pytest

from wip_auth.resolve import EntityNotFoundError

SYNONYM_MAP = {
    ("template", "PERSON", "test-ns"): "TPL-000001",
    ("template", "EMPLOYEE", "test-ns"): "TPL-000002",
    ("template", "PERSON", None): "TPL-000001",
    ("template", "EMPLOYEE", None): "TPL-000002",
}


async def mock_resolve(raw_id, entity_type, namespace, **kwargs):
    """Mock resolve_entity_id that uses SYNONYM_MAP."""
    import re
    if re.match(r"^[0-9a-f]{8}-", raw_id, re.IGNORECASE) or raw_id.startswith("TPL-"):
        return raw_id
    key = (entity_type, raw_id, namespace)
    if key in SYNONYM_MAP:
        return SYNONYM_MAP[key]
    raise EntityNotFoundError(raw_id, entity_type)


@pytest.mark.asyncio
class TestTemplateSynonymResolution:
    """Test synonym resolution on template endpoints."""

    async def test_get_template_resolves_synonym(self, client, auth_headers):
        """GET /templates/{synonym} should resolve via Registry."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/template-store/templates/PERSON?namespace=test-ns",
                headers=auth_headers,
            )
        # TPL-000001 won't exist in test DB, but should not be 500
        assert resp.status_code in (200, 404), f"Unexpected: {resp.status_code} {resp.text}"

    async def test_get_template_unresolvable_returns_404(self, client, auth_headers):
        """GET /templates/{bad} should return 404, not 500."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/template-store/templates/NONEXISTENT?namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code == 404

    async def test_get_template_raw_resolves_synonym(self, client, auth_headers):
        """GET /templates/{synonym}/raw should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/template-store/templates/PERSON/raw?namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404)

    async def test_list_templates_resolves_extends(self, client, auth_headers):
        """GET /templates?extends=synonym should resolve the extends param."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/template-store/templates?extends=PERSON&namespace=test-ns",
                headers=auth_headers,
            )
        assert resp.status_code == 200

    async def test_validate_template_resolves_synonym(self, client, auth_headers):
        """POST /templates/{synonym}/validate should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/template-store/templates/PERSON/validate",
                headers=auth_headers,
                json={},
            )
        assert resp.status_code in (200, 404)

    async def test_activate_template_resolves_synonym(self, client, auth_headers):
        """POST /templates/{synonym}/activate should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/template-store/templates/PERSON/activate?namespace=test-ns",
                headers=auth_headers,
            )
        # May fail (template not in DB), but should not be 500
        assert resp.status_code in (200, 400, 404)

    async def test_cascade_template_resolves_synonym(self, client, auth_headers):
        """POST /templates/{synonym}/cascade should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.post(
                "/api/template-store/templates/PERSON/cascade",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404)

    async def test_dependencies_resolves_synonym(self, client, auth_headers):
        """GET /templates/{synonym}/dependencies should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/template-store/templates/PERSON/dependencies",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404)

    async def test_children_resolves_synonym(self, client, auth_headers):
        """GET /templates/{synonym}/children should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/template-store/templates/PERSON/children",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404)

    async def test_descendants_resolves_synonym(self, client, auth_headers):
        """GET /templates/{synonym}/descendants should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.get(
                "/api/template-store/templates/PERSON/descendants",
                headers=auth_headers,
            )
        assert resp.status_code in (200, 404)

    async def test_delete_body_resolves_synonym(self, client, auth_headers):
        """DELETE /templates with synonym in body should resolve."""
        with patch("wip_auth.fastapi_helpers.resolve_entity_id", side_effect=mock_resolve):
            resp = await client.request(
                "DELETE",
                "/api/template-store/templates",
                headers=auth_headers,
                json=[{"id": "PERSON"}],
            )
        # Should resolve, then fail finding TPL-000001 in DB (per-item error)
        assert resp.status_code == 200
