"""A file field's allowed_types cannot be empty, and a failed create gives
its Registry identity back.

- ``file_config.allowed_types: []`` was accepted and stored, and
  document-store read it as "every MIME type" (``or ["*/*"]``). An empty list
  is now refused at the write; omitting it still means every type.
- create_template registers the template's identity before inserting the
  row. When the insert failed nothing was rolled back, and when the
  auto-synonym failed only the row was: the Registry kept the entry and its
  composite key, so the next create of the same (namespace, value) collided
  with an entry that owned no template. A create that fails after minting its
  entry now releases it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from registry.models.entry import RegistryEntry
from template_store.models.template import Template
from template_store.services.registry_client import RegistryClient, RegistryError

API = "/api/template-store/templates"


async def _post(client, headers, payload) -> dict:
    resp = await client.post(API, headers=headers, json=[payload])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


def _entity(value: str, *fields: dict) -> dict:
    return {"namespace": "wip", "value": value, "label": value.title(),
            "fields": [{"name": "id", "type": "string", "label": "Id", "mandatory": True},
                       *fields]}


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [
    {"name": "doc", "label": "Doc", "type": "file", "file_config": {"allowed_types": []}},
    {"name": "docs", "label": "Docs", "type": "array", "array_item_type": "file",
     "array_file_config": {"allowed_types": []}},
])
async def test_an_empty_allowed_types_is_refused(client: AsyncClient, auth_headers, field):
    item = await _post(client, auth_headers, _entity(f"C1171_EMPTY_{field['name']}".upper(), field))
    assert item["status"] == "error", item
    assert item["details"]["rule"] == "allowed_types_empty", item


@pytest.mark.asyncio
async def test_an_omitted_allowed_types_still_accepts_every_type(client: AsyncClient, auth_headers):
    item = await _post(client, auth_headers, _entity(
        "C1171_ANY", {"name": "doc", "label": "Doc", "type": "file", "file_config": {}},
    ))
    assert item["status"] == "created", item
    stored = await Template.find_one({"template_id": item["id"]})
    assert stored.fields[1].file_config.allowed_types == ["*/*"]


async def _entries(value: str) -> int:
    return await RegistryEntry.find(
        {"namespace": "wip", "entity_type": "templates", "primary_composite_key.value": value},
    ).count()


@pytest.mark.asyncio
async def test_a_failed_insert_releases_the_identity(client: AsyncClient, auth_headers):
    with patch.object(Template, "insert", side_effect=RuntimeError("mongo went away")):
        resp = await client.post(API, headers=auth_headers, json=[_entity("C1171_INSERT")])
    assert resp.json()["results"][0]["error_code"] == "internal_error", resp.text
    assert await _entries("C1171_INSERT") == 0
    item = await _post(client, auth_headers, _entity("C1171_INSERT"))
    assert item["status"] == "created", item


@pytest.mark.asyncio
async def test_a_failed_auto_synonym_releases_the_identity(client: AsyncClient, auth_headers):
    with patch.object(RegistryClient, "register_auto_synonym",
                      side_effect=RegistryError("registry hiccup")):
        resp = await client.post(API, headers=auth_headers, json=[_entity("C1171_SYN")])
    assert resp.json()["results"][0]["status"] == "error", resp.text
    assert await Template.find({"value": "C1171_SYN"}).count() == 0
    assert await _entries("C1171_SYN") == 0
    item = await _post(client, auth_headers, _entity("C1171_SYN"))
    assert item["status"] == "created", item
