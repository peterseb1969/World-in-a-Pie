"""Changing a key's namespaces through PATCH changes its grants with them.

A key's access is its grants; its `namespaces` list only caps where it may
act. PATCH set the list and touched no grant: a namespace it added was one
the key could not read, and the grant on a namespace it dropped stayed
(dormant), to come back with a later widen. Widening and narrowing one
namespace at a time already wrote and revoked the grant; PATCH now does the
same for every namespace it adds or drops.
"""

from __future__ import annotations

import uuid

import pytest

from registry.models.grant import NamespaceGrant

BASE = "/api/registry"


async def _grants(name: str) -> dict[str, str]:
    rows = await NamespaceGrant.find(
        {"subject_type": "api_key", "subject": {"$in": [name, f"apikey:{name}"]}}
    ).to_list()
    return {g.namespace: g.permission for g in rows}


@pytest.mark.asyncio
async def test_patching_namespaces_grants_the_added_and_revokes_the_dropped(client, auth_headers):
    name = f"c1270-{uuid.uuid4().hex[:8]}"
    created = await client.post(f"{BASE}/api-keys", headers=auth_headers, json={
        "name": name, "groups": [], "namespaces": ["vendor1"], "grant_permission": "write",
    })
    assert created.status_code == 201, created.text
    assert await _grants(name) == {"vendor1": "write"}

    widened = await client.patch(f"{BASE}/api-keys/{name}", headers=auth_headers,
                                 json={"namespaces": ["vendor1", "vendor2"]})
    assert widened.status_code == 200, widened.text
    # The namespace it keeps keeps its grant; the one it adds reads.
    assert await _grants(name) == {"vendor1": "write", "vendor2": "read"}

    narrowed = await client.patch(f"{BASE}/api-keys/{name}", headers=auth_headers,
                                  json={"namespaces": ["vendor2"]})
    assert narrowed.status_code == 200, narrowed.text
    assert await _grants(name) == {"vendor2": "read"}

    # Widening again does not bring the old write back.
    await client.patch(f"{BASE}/api-keys/{name}", headers=auth_headers,
                       json={"namespaces": ["vendor1", "vendor2"]})
    assert await _grants(name) == {"vendor1": "read", "vendor2": "read"}


@pytest.mark.asyncio
async def test_patching_other_fields_leaves_the_grants_alone(client, auth_headers):
    name = f"c1270-{uuid.uuid4().hex[:8]}"
    await client.post(f"{BASE}/api-keys", headers=auth_headers, json={
        "name": name, "groups": [], "namespaces": ["vendor1"], "grant_permission": "write",
    })
    patched = await client.patch(f"{BASE}/api-keys/{name}", headers=auth_headers,
                                 json={"description": "renamed"})
    assert patched.status_code == 200, patched.text
    assert await _grants(name) == {"vendor1": "write"}
