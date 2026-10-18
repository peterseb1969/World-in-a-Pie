"""Owner-service flags: only the service that owns an object may bypass.

- `owner_merge` on POST /synonyms/merge: term entries merge only this way
  (def-store's term merge), because a Registry-only merge would leave the
  absorbed term's value resolving to it. Other entity types are unaffected.
- `owner_hard_deleted` on DELETE /entries: the owning service already
  removed the object under a deletion_mode exemption (mutable-terminology
  terms), so the entry follows even in a 'retain' namespace.

Both are honored for privileged callers (wip-admins / wip-services) only.
The master key is privileged; a runtime key with no groups is not.
"""

import pytest
from httpx import AsyncClient

KEYS = "/api/registry/api-keys"


async def _register(client, headers, value, entity_type="terms"):
    resp = await client.post("/api/registry/entries/register", headers=headers, json=[{
        "namespace": "default", "entity_type": entity_type, "composite_key": {"value": value},
    }])
    res = resp.json()["results"][0]
    assert res["status"] == "created", res
    return res["registry_id"]


async def _plain_key(client, headers, name):
    resp = await client.post(KEYS, headers=headers, json={"name": name, "groups": [], "namespaces": ["default"]})
    assert resp.status_code in (200, 201), resp.text
    return {"X-API-Key": resp.json()["plaintext_key"]}


async def _merge(client, headers, preferred, deprecated, **flags):
    resp = await client.post("/api/registry/synonyms/merge", headers=headers, json=[{
        "preferred_id": preferred, "deprecated_id": deprecated, **flags,
    }])
    return resp.json()["results"][0]


async def _delete(client, headers, entry_id, **flags):
    resp = await client.request("DELETE", "/api/registry/entries", headers=headers, json=[{
        "entry_id": entry_id, "hard_delete": True, **flags,
    }])
    return resp.json()["results"][0]


@pytest.mark.asyncio
async def test_term_entries_refuse_a_plain_merge(client: AsyncClient, auth_headers: dict):
    a, b = await _register(client, auth_headers, "OF_A"), await _register(client, auth_headers, "OF_B")
    res = await _merge(client, auth_headers, a, b)
    assert res["status"] == "error"
    assert "/api/def-store/terms/merge" in res["error"]


@pytest.mark.asyncio
async def test_owner_merge_is_ignored_for_unprivileged_keys(client: AsyncClient, auth_headers: dict):
    a, b = await _register(client, auth_headers, "OF_C"), await _register(client, auth_headers, "OF_D")
    plain = await _plain_key(client, auth_headers, "owner-merge-plain")
    assert (await _merge(client, plain, a, b, owner_merge=True))["status"] == "error"


@pytest.mark.asyncio
async def test_owner_merge_merges_term_entries_for_privileged_callers(client: AsyncClient, auth_headers: dict):
    a, b = await _register(client, auth_headers, "OF_E"), await _register(client, auth_headers, "OF_F")
    assert (await _merge(client, auth_headers, a, b, owner_merge=True))["status"] == "merged"


@pytest.mark.asyncio
async def test_other_entity_types_merge_as_before(client: AsyncClient, auth_headers: dict):
    a = await _register(client, auth_headers, "OF_G", entity_type="documents")
    b = await _register(client, auth_headers, "OF_H", entity_type="documents")
    assert (await _merge(client, auth_headers, a, b))["status"] == "merged"


@pytest.mark.asyncio
async def test_owner_hard_deleted_bypasses_retain_for_privileged_callers(client: AsyncClient, auth_headers: dict):
    gated = await _register(client, auth_headers, "OF_I")
    assert (await _delete(client, auth_headers, gated))["status"] == "error"  # retain gate stands
    assert (await _delete(client, auth_headers, gated, owner_hard_deleted=True))["status"] == "deleted"


@pytest.mark.asyncio
async def test_owner_hard_deleted_is_ignored_for_unprivileged_keys(client: AsyncClient, auth_headers: dict):
    entry = await _register(client, auth_headers, "OF_J")
    plain = await _plain_key(client, auth_headers, "owner-delete-plain")
    res = await _delete(client, plain, entry, owner_hard_deleted=True)
    assert res["status"] != "deleted", res
