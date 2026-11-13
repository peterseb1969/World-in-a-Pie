"""An unscoped candidate lookup leaves out a namespace locked for deletion.

A namespace being deleted is closed to everyone, administrators and service
keys included, and every listing that names no namespace already reads
"every namespace but a locked one" for them. POST /entries/lookup/candidates
still queried every namespace for such a caller when the item named none,
so a locked namespace's entries came back as candidates and counted in
`total`. It now scopes the query to the namespaces the caller can read.
"""

import uuid

import pytest

from registry.models.namespace import Namespace

NS = "/api/registry/namespaces"
CANDIDATES = "/api/registry/entries/lookup/candidates"


async def _namespace(client, headers) -> str:
    prefix = f"l1226-{uuid.uuid4().hex[:8]}"
    r = await client.put(f"{NS}/{prefix}", json={"deletion_mode": "full"}, headers=headers)
    assert r.status_code == 200, r.text
    return prefix


async def _register(client, headers, prefix: str, value: str) -> str:
    r = await client.post(
        "/api/registry/entries/register",
        json=[{"namespace": prefix, "entity_type": "terms", "composite_key": {"value": value}}],
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["results"][0]["registry_id"]


async def _lock(prefix: str) -> None:
    await Namespace.get_motor_collection().update_one({"prefix": prefix}, {"$set": {"status": "locked"}})


async def _candidates(client, headers, value: str) -> dict:
    r = await client.post(CANDIDATES, json=[{"value": value}], headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["results"][0]


@pytest.mark.asyncio
async def test_a_locked_namespaces_entries_are_no_candidates_for_an_administrator(client, auth_headers):
    open_ns, locked_ns = await _namespace(client, auth_headers), await _namespace(client, auth_headers)
    value = f"v1226-{uuid.uuid4().hex[:8]}"
    open_id = await _register(client, auth_headers, open_ns, value)
    await _register(client, auth_headers, locked_ns, value)
    assert (await _candidates(client, auth_headers, value))["total"] == 2

    await _lock(locked_ns)
    result = await _candidates(client, auth_headers, value)
    assert result["total"] == 1
    assert [c["entry_id"] for c in result["candidates"]] == [open_id]


@pytest.mark.asyncio
async def test_a_locked_namespaces_entry_id_is_not_found(client, auth_headers):
    prefix = await _namespace(client, auth_headers)
    entry_id = await _register(client, auth_headers, prefix, f"v1226-{uuid.uuid4().hex[:8]}")
    await _lock(prefix)
    assert (await _candidates(client, auth_headers, entry_id))["status"] == "not_found"
