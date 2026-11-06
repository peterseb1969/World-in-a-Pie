"""A canonical id resolves only as its own entity type (CASE-1088).

/resolve's canonical-id path (Path 1) looked an entry up by entry_id and
status only, so a template's id resolved as a terminology, a document's as a
term. With entity_type given, Path 1 now applies it too; without it, the
behaviour is unchanged (older clients).
"""

import pytest
from httpx import AsyncClient

BASE = "/api/registry"


async def _register(client: AsyncClient, headers: dict, value: str, entity_type: str) -> str:
    resp = await client.post(
        f"{BASE}/entries/register",
        json=[{"namespace": "default", "entity_type": entity_type, "composite_key": {"value": value}}],
        headers=headers,
    )
    result = resp.json()["results"][0]
    assert result["status"] == "created", result
    return result["registry_id"]


async def _resolve(client: AsyncClient, headers: dict, **item) -> dict:
    resp = await client.post(f"{BASE}/entries/resolve", json=[item], headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


@pytest.mark.asyncio
async def test_an_id_of_another_type_is_not_found(client: AsyncClient, auth_headers: dict):
    template_id = await _register(client, auth_headers, "c1088-template", "templates")
    result = await _resolve(client, auth_headers, entry_id=template_id, entity_type="terminologies")
    assert result["status"] == "not_found", result


@pytest.mark.asyncio
async def test_an_id_of_its_own_type_is_found(client: AsyncClient, auth_headers: dict):
    template_id = await _register(client, auth_headers, "c1088-own", "templates")
    result = await _resolve(client, auth_headers, entry_id=template_id, entity_type="templates")
    assert (result["status"], result["entry_id"]) == ("found", template_id)


@pytest.mark.asyncio
async def test_without_a_type_an_id_resolves_as_before(client: AsyncClient, auth_headers: dict):
    template_id = await _register(client, auth_headers, "c1088-untyped", "templates")
    result = await _resolve(client, auth_headers, entry_id=template_id)
    assert (result["status"], result["entry_id"]) == ("found", template_id)


@pytest.mark.asyncio
async def test_a_value_matching_another_types_id_falls_through_to_its_key(
    client: AsyncClient, auth_headers: dict,
):
    """A non-UUID value is sent as both entry_id and composite key: an id of
    another type no longer wins, and the value's own key answers."""
    doc_id = await _register(client, auth_headers, "c1088-doc", "documents")
    own = await _register(client, auth_headers, doc_id, "templates")
    result = await _resolve(
        client, auth_headers,
        entry_id=doc_id, composite_key={"value": doc_id}, entity_type="templates",
    )
    assert (result["status"], result["entry_id"]) == ("found", own)
