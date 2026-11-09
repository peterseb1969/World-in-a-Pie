"""A namespace locked for deletion is closed to everyone, administrators too.

The Registry's resolution already answered "none" for a locked namespace
before its superadmin rule, but its entry and search doors let every
privileged caller (wip-admins, wip-services) through without asking, and the
other services answered wip-admins "admin" themselves. Now every door asks,
and taking the lock tells every service to drop the permissions it cached.
Provenance: CASE-1222.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from registry.api.grants import NamespaceAccess, _resolve_permission
from registry.models.namespace import Namespace
from registry.services.namespace_deletion import NamespaceDeletionService
from wip_auth import UserIdentity

NS = "/api/registry/namespaces"


def _identity(group: str) -> UserIdentity:
    return UserIdentity(user_id=f"u-{group}", username=group, groups=[group], auth_method="api_key")


async def _namespace(client, headers) -> str:
    prefix = f"l1222-{uuid.uuid4().hex[:8]}"
    r = await client.put(f"{NS}/{prefix}", json={"deletion_mode": "full"}, headers=headers)
    assert r.status_code == 200, r.text
    return prefix


async def _lock(prefix: str) -> None:
    await Namespace.get_motor_collection().update_one({"prefix": prefix}, {"$set": {"status": "locked"}})


@pytest.mark.asyncio
async def test_an_administrator_gets_none_on_a_locked_namespace(client, auth_headers):
    prefix = await _namespace(client, auth_headers)
    admin = _identity("wip-admins")
    assert await _resolve_permission(admin, prefix) == "admin"
    await _lock(prefix)
    assert await _resolve_permission(admin, prefix) == "none"


@pytest.mark.asyncio
@pytest.mark.parametrize("group", ["wip-admins", "wip-services"])
async def test_privileged_callers_are_refused_a_locked_namespace(client, auth_headers, group):
    open_ns = await _namespace(client, auth_headers)
    locked_ns = await _namespace(client, auth_headers)
    await _lock(locked_ns)
    access = NamespaceAccess(_identity(group))
    assert access.unrestricted
    assert await access.allows(open_ns, "write")
    assert not await access.allows(locked_ns, "read")


@pytest.mark.asyncio
async def test_an_entry_of_a_locked_namespace_is_not_found_by_the_master_key(client, auth_headers):
    prefix = await _namespace(client, auth_headers)
    r = await client.post(
        "/api/registry/entries/register",
        json=[{"namespace": prefix, "entity_type": "terms", "composite_key": {"k": "v"}}],
        headers=auth_headers,
    )
    entry_id = r.json()["results"][0]["registry_id"]
    assert (await client.get(f"/api/registry/entries/{entry_id}", headers=auth_headers)).status_code == 200
    await _lock(prefix)
    r = await client.get(f"/api/registry/entries/{entry_id}", headers=auth_headers)
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_taking_the_lock_drops_every_services_cached_permissions(client, auth_headers):
    prefix = await _namespace(client, auth_headers)
    svc = NamespaceDeletionService()
    announced = AsyncMock()
    stop = RuntimeError("stop after the lock")
    with (
        patch("registry.services.namespace_deletion.access_changed", announced),
        patch.object(svc, "_build_journal", AsyncMock(side_effect=stop)),
        pytest.raises(RuntimeError, match="stop after the lock"),
    ):
        await svc.start_deletion(prefix, requested_by="t")
    assert (await Namespace.find_one({"prefix": prefix})).status == "locked"
    announced.assert_awaited_once_with(keys=False)
