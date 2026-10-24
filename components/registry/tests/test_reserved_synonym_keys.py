"""Synonym keys starting with "@" are reserved for system-generated synonyms.

Every synonym is a qualified key-value pair. Document-store qualifies a
document's identity-values synonym with "@template"; if any caller could
register that shape, an explicit synonym could claim the key a document
will need. So only privileged (system) callers may write "@" keys.
"""

import uuid

import pytest
from httpx import AsyncClient

BASE = "/api/registry"


async def _entry(client: AsyncClient, auth_headers: dict) -> str:
    resp = await client.post(f"{BASE}/entries/register", headers=auth_headers, json=[{
        "namespace": "default", "entity_type": "documents",
        "composite_key": {"value": f"reserved-{uuid.uuid4().hex[:8]}"},
    }])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]["registry_id"]


async def _writer_key(client: AsyncClient, auth_headers: dict) -> dict:
    resp = await client.post(f"{BASE}/api-keys", headers=auth_headers, json={
        "name": f"reserved-{uuid.uuid4().hex[:8]}", "groups": [],
        "namespaces": ["default"], "grant_permission": "write",
    })
    assert resp.status_code == 201, resp.text
    return {"X-API-Key": resp.json()["plaintext_key"]}


async def _add(client: AsyncClient, headers: dict, entry_id: str, key: dict) -> dict:
    resp = await client.post(f"{BASE}/synonyms/add", headers=headers, json=[{
        "target_id": entry_id, "synonym_namespace": "default",
        "synonym_entity_type": "documents", "synonym_composite_key": key,
    }])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


@pytest.mark.asyncio
async def test_a_non_privileged_caller_cannot_use_a_reserved_key(client: AsyncClient, auth_headers: dict):
    entry_id = await _entry(client, auth_headers)
    writer = await _writer_key(client, auth_headers)
    result = await _add(client, writer, entry_id, {"@template": "CHAPTER", "title": "X"})
    assert result["status"] == "forbidden"
    assert "reserved" in result["error"]


@pytest.mark.asyncio
async def test_the_same_caller_may_add_an_ordinary_qualified_synonym(client: AsyncClient, auth_headers: dict):
    entry_id = await _entry(client, auth_headers)
    writer = await _writer_key(client, auth_headers)
    result = await _add(client, writer, entry_id, {"erp_id": f"SAP-{uuid.uuid4().hex[:6]}"})
    assert result["status"] == "added", result


@pytest.mark.asyncio
async def test_a_system_caller_may_use_a_reserved_key(client: AsyncClient, auth_headers: dict):
    entry_id = await _entry(client, auth_headers)
    result = await _add(client, auth_headers, entry_id, {"@template": "CHAPTER", "title": uuid.uuid4().hex})
    assert result["status"] == "added", result
