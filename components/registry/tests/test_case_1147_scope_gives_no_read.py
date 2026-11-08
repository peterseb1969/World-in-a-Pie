"""A key's namespace list is a scope, not a permission.

A runtime key's ``namespaces`` list says where the key may act at all; what
it may do there comes from its grants. The list used to give read by itself
on every namespace it named, with no grant — so access survived everything
that ends a grant: deleting the namespace (which purges its grants) and
creating another under the name, a system restore writing the grant
orphaned because the namespace here is a different one, and revoking the
key's grant. Now key creation writes a read grant on each listed namespace
it gives no other permission on, the list gives nothing, and a one-off
backfill wrote the read grants older keys relied on.
"""

import uuid

import pytest

from registry.models.api_key import StoredAPIKey
from registry.models.grant import NamespaceGrant

BASE = "/api/registry"


def _p(kind: str) -> str:
    return f"c1147-{kind}-{uuid.uuid4().hex[:8]}"


async def _namespace(client, headers, prefix, **body):
    r = await client.put(f"{BASE}/namespaces/{prefix}", headers=headers, json=body)
    assert r.status_code == 200, r.text


async def _key(client, headers, namespaces, **body) -> tuple[str, dict]:
    name = f"c1147-{uuid.uuid4().hex[:8]}"
    r = await client.post(f"{BASE}/api-keys", headers=headers, json={
        "name": name, "groups": [], "namespaces": namespaces, **body,
    })
    assert r.status_code == 201, r.text
    return name, {"X-API-Key": r.json()["plaintext_key"]}


async def _permission(client, key_headers, prefix) -> str:
    r = await client.get(f"{BASE}/my/namespaces/{prefix}/permission", headers=key_headers)
    if r.status_code == 404:
        return "none"
    assert r.status_code == 200, r.text
    return r.json()["permission"]


@pytest.mark.asyncio
async def test_a_key_created_without_a_permission_reads_through_a_grant(client, auth_headers):
    prefix = _p("a")
    await _namespace(client, auth_headers, prefix)
    name, key = await _key(client, auth_headers, [prefix])

    assert await _permission(client, key, prefix) == "read"
    grant = await NamespaceGrant.find_one({"namespace": prefix, "subject": name})
    assert grant is not None and grant.permission == "read"


@pytest.mark.asyncio
async def test_revoking_the_keys_grant_ends_its_read(client, auth_headers):
    prefix = _p("rev")
    await _namespace(client, auth_headers, prefix)
    name, key = await _key(client, auth_headers, [prefix])
    r = await client.request(
        "DELETE", f"{BASE}/namespaces/{prefix}/grants", headers=auth_headers,
        json=[{"subject": name, "subject_type": "api_key"}],
    )
    assert r.status_code == 200, r.text

    assert await _permission(client, key, prefix) == "none"


@pytest.mark.asyncio
async def test_a_namespace_deleted_and_created_again_is_not_readable(client, auth_headers):
    prefix = _p("del")
    await _namespace(client, auth_headers, prefix, deletion_mode="full")
    _name, key = await _key(client, auth_headers, [prefix], grant_permission="write")
    assert await _permission(client, key, prefix) == "write"

    assert (await client.delete(f"{BASE}/namespaces/{prefix}", headers=auth_headers)).status_code == 200
    await _namespace(client, auth_headers, prefix)

    assert await _permission(client, key, prefix) == "none"


@pytest.mark.asyncio
async def test_an_orphaned_grant_gives_nothing_even_in_scope(client, auth_headers):
    """What a system restore writes when the namespace here is another one."""
    prefix = _p("orph")
    await _namespace(client, auth_headers, prefix)
    name, key = await _key(client, auth_headers, [prefix])
    grant = await NamespaceGrant.find_one({"namespace": prefix, "subject": name})
    grant.orphaned = True
    await grant.save()

    assert await _permission(client, key, prefix) == "none"


@pytest.mark.asyncio
async def test_the_scope_still_caps_what_grants_give(client, auth_headers):
    inside, outside = _p("in"), _p("out")
    for prefix in (inside, outside):
        await _namespace(client, auth_headers, prefix)
    name, key = await _key(client, auth_headers, [inside], grant_permission="write")
    # A grant on a namespace outside the key's scope gives nothing.
    r = await client.post(f"{BASE}/namespaces/{outside}/grants", headers=auth_headers, json=[
        {"subject": name, "subject_type": "api_key", "permission": "write"},
    ])
    assert r.status_code == 200, r.text

    assert await _permission(client, key, inside) == "write"
    assert await _permission(client, key, outside) == "none"


@pytest.mark.asyncio
async def test_the_backfill_gives_older_keys_their_read_grants_once(client, auth_headers):
    from registry.services import key_scope_grants

    here, gone = _p("bf"), _p("bfgone")
    await _namespace(client, auth_headers, here)
    name = f"c1147-legacy-{uuid.uuid4().hex[:8]}"
    await StoredAPIKey(
        name=name, key_hash="x" * 60, namespaces=[here, gone], groups=[],
    ).insert()
    await key_scope_grants.reset_marker_for_tests()

    written = await key_scope_grants.backfill_scope_read_grants()
    assert written >= 1
    grant = await NamespaceGrant.find_one({"namespace": here, "subject": name})
    assert grant is not None and grant.permission == "read"
    # A namespace that does not exist gets no grant.
    assert await NamespaceGrant.find_one({"namespace": gone, "subject": name}) is None

    # Once only: a later run changes nothing, so a grant purged by a deletion
    # is never written back.
    await grant.delete()
    assert await key_scope_grants.backfill_scope_read_grants() == 0
    assert await NamespaceGrant.find_one({"namespace": here, "subject": name}) is None
