"""CASE-432 Site 1 — template-store `extends` resolves synonyms via the Registry.

Before the fix, `create_template` (and the version path) resolved `extends`
only by direct MongoDB lookup (`{template_id}` then `{namespace, value}`), so a
registered synonym for the parent that matched neither its canonical id nor its
value would NOT resolve — `extends` silently didn't honor synonyms (Vision
§"References Must Resolve"). The fix resolves `extends` through the shared
`resolve_entity_id` first, then falls through to the legacy lookups on a miss.

These tests patch `template_service.resolve_entity_id` to map a custom alias
(matching neither template_id nor value) to the real parent id, and assert the
child is created with `extends` canonicalised to the parent — which the
pre-fix MongoDB-only path could not do.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from wip_auth.resolve import EntityNotFoundError

_PATCH_TARGET = "template_store.services.template_service.resolve_entity_id"


async def _create_parent(client: AsyncClient, auth_headers: dict, value: str) -> str:
    resp = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[{
            "namespace": "wip",
            "value": value,
            "label": f"{value} parent",
            "identity_fields": ["id_field"],
            "fields": [{"name": "id_field", "label": "ID", "type": "string", "mandatory": True}],
        }],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]["id"]


@pytest.mark.asyncio
class TestExtendsSynonymResolution:
    async def test_extends_resolves_custom_synonym(self, client: AsyncClient, auth_headers: dict):
        parent_id = await _create_parent(client, auth_headers, "EXT_PARENT")

        async def _resolve(raw_id, entity_type, namespace, **kwargs):
            # A custom synonym for the parent — matches neither its canonical
            # id nor its value, so only Registry resolution can map it.
            if raw_id == "EXT_PARENT_ALIAS":
                return parent_id
            if raw_id == parent_id:
                return parent_id
            raise EntityNotFoundError(raw_id, entity_type)

        with patch(_PATCH_TARGET, side_effect=_resolve):
            resp = await client.post(
                "/api/template-store/templates",
                headers=auth_headers,
                json=[{
                    "namespace": "wip",
                    "value": "EXT_CHILD",
                    "label": "Child via aliased extends",
                    "extends": "EXT_PARENT_ALIAS",
                    "fields": [{"name": "child_field", "label": "Child", "type": "string"}],
                }],
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["succeeded"] == 1, data
        child_id = data["results"][0]["id"]

        raw = await client.get(
            f"/api/template-store/templates/{child_id}/raw", headers=auth_headers
        )
        assert raw.status_code == 200, raw.text
        # extends was canonicalised to the parent's real id via the Registry.
        assert raw.json()["extends"] == parent_id

    async def test_extends_miss_still_falls_through(self, client: AsyncClient, auth_headers: dict):
        """On a Registry miss, the legacy lookups still run — extends by the
        parent's real value continues to work (no regression)."""
        parent_id = await _create_parent(client, auth_headers, "EXT_PARENT_FALLTHROUGH")

        async def _resolve(raw_id, entity_type, namespace, **kwargs):
            if raw_id == parent_id:
                return parent_id
            raise EntityNotFoundError(raw_id, entity_type)  # alias/value all miss

        with patch(_PATCH_TARGET, side_effect=_resolve):
            resp = await client.post(
                "/api/template-store/templates",
                headers=auth_headers,
                json=[{
                    "namespace": "wip",
                    "value": "EXT_CHILD_FALLTHROUGH",
                    "label": "Child via value extends",
                    "extends": "EXT_PARENT_FALLTHROUGH",  # the parent's value
                    "fields": [{"name": "child_field", "label": "Child", "type": "string"}],
                }],
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["succeeded"] == 1, data
        child_id = data["results"][0]["id"]
        raw = await client.get(
            f"/api/template-store/templates/{child_id}/raw", headers=auth_headers
        )
        assert raw.json()["extends"] == parent_id
