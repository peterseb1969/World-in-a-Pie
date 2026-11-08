"""A namespace locked for deletion stays locked until the deletion is done.

A deletion locks the namespace before it removes anything; its recovery and
its clean-up steps read the lock to tell an unfinished deletion from a
namespace created again. Archive set a locked namespace to 'archived' (the
deletion then skipped its PostgreSQL step and document-store refused its
storage delete); the upsert and the deletion-mode PATCH changed a locked
namespace; and every one of them, the lock included, read the record and
saved it whole, so a save begun before the lock could write the old status
back over it. Provenance: CASE-1124.
"""

import uuid

import pytest

from registry.models.namespace import Namespace
from registry.services.namespace_deletion import NamespaceDeletionService

NS = "/api/registry/namespaces"


async def _locked(client, headers) -> str:
    prefix = f"lock-{uuid.uuid4().hex[:8]}"
    r = await client.put(f"{NS}/{prefix}", json={"deletion_mode": "full"}, headers=headers)
    assert r.status_code == 200, r.text
    await Namespace.get_motor_collection().update_one({"prefix": prefix}, {"$set": {"status": "locked"}})
    return prefix


async def _status(prefix: str) -> str:
    return (await Namespace.find_one({"prefix": prefix})).status


@pytest.mark.asyncio
async def test_archive_refuses_a_locked_namespace(client, auth_headers):
    prefix = await _locked(client, auth_headers)
    r = await client.post(f"{NS}/{prefix}/archive", headers=auth_headers)
    assert r.status_code == 409, r.text
    assert await _status(prefix) == "locked"


@pytest.mark.asyncio
async def test_the_upsert_refuses_a_locked_namespace(client, auth_headers):
    prefix = await _locked(client, auth_headers)
    for params in ({}, {"dry_run": "true"}):
        r = await client.put(f"{NS}/{prefix}", json={"description": "changed"}, params=params,
                             headers=auth_headers)
        assert r.status_code == 409, (params, r.text)
    ns = await Namespace.find_one({"prefix": prefix})
    assert (ns.status, ns.description) == ("locked", "")


@pytest.mark.asyncio
async def test_the_deletion_mode_patch_refuses_a_locked_namespace(client, auth_headers):
    prefix = await _locked(client, auth_headers)
    r = await client.patch(f"{NS}/{prefix}", params={"deletion_mode": "retain"}, headers=auth_headers)
    assert r.status_code == 409, r.text
    ns = await Namespace.find_one({"prefix": prefix})
    assert (ns.status, ns.deletion_mode) == ("locked", "full")


@pytest.mark.asyncio
async def test_a_write_begun_before_the_lock_does_not_undo_it(client, auth_headers):
    """The writer read the namespace unlocked; the deletion locked it before
    the write."""
    from registry.models.namespace import set_unless_locked

    prefix = f"lock-{uuid.uuid4().hex[:8]}"
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=auth_headers)).status_code == 200
    stale = await Namespace.find_one({"prefix": prefix})
    await Namespace.get_motor_collection().update_one({"prefix": prefix}, {"$set": {"status": "locked"}})
    assert await set_unless_locked(stale, {"status": "archived"}) is False
    assert await _status(prefix) == "locked"


@pytest.mark.asyncio
async def test_a_second_deletion_cannot_take_the_lock(client, auth_headers):
    """Two deletions both read the namespace unlocked; only one may lock it."""
    prefix = f"lock-{uuid.uuid4().hex[:8]}"
    r = await client.put(f"{NS}/{prefix}", json={"deletion_mode": "full"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    svc = NamespaceDeletionService()

    async def the_other_deletion_locks_it(_prefix):
        await Namespace.get_motor_collection().update_one({"prefix": prefix}, {"$set": {"status": "locked"}})
        return []

    svc._check_inbound_references = the_other_deletion_locks_it
    with pytest.raises(ValueError, match="already locked"):
        await svc.start_deletion(prefix, requested_by="t")
