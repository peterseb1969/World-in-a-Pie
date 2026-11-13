"""Every way a namespace deletion can stop leaves a way to finish it.

A failed step ended the journal 'failed', and degraded best-effort steps
ended it 'completed_with_warnings': resume and the recovery loop picked only
'in_progress', so the namespace stayed locked (and invisible) for good. A
stop between the lock and the journal left a lock with no journal. Every file
key sat on one step, beyond MongoDB's document size for large namespaces.
Two runs of one journal could overlap, the loop could retire while a request
still ran a deferring journal, a connection reset was recorded as a lasting
degradation, and re-creation was judged by status. Provenance: CASE-1120.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import registry.services.namespace_deletion as mod
from registry.models.deletion_journal import DeletionJournal, DeletionStep
from registry.models.namespace import Namespace
from registry.services.namespace_deletion import NamespaceDeletionService

NS = "/api/registry/namespaces"


def _svc(monkeypatch) -> NamespaceDeletionService:
    svc = NamespaceDeletionService()
    monkeypatch.setattr(svc, "start_recovery", lambda: None)
    return svc


async def _namespace(client, headers) -> str:
    prefix = f"del-{uuid.uuid4().hex[:8]}"
    r = await client.put(f"{NS}/{prefix}", json={"deletion_mode": "full"}, headers=headers)
    assert r.status_code == 200, r.text
    return prefix


@pytest.mark.asyncio
async def test_a_failed_deletion_is_resumed(client, auth_headers, monkeypatch):
    prefix = await _namespace(client, auth_headers)
    svc = _svc(monkeypatch)
    real = svc._exec_mongodb_step
    calls = {"n": 0}

    async def fail_once(step):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk full")
        return await real(step)

    monkeypatch.setattr(svc, "_exec_mongodb_step", fail_once)
    with pytest.raises(RuntimeError):
        await svc.start_deletion(prefix, requested_by="t")
    journal = await DeletionJournal.find_one({"namespace": prefix})
    assert journal.status == "failed"
    assert (await Namespace.find_one({"prefix": prefix})).status == "locked"

    resumed = await svc.resume_deletion(prefix)
    assert resumed.status == "completed"
    assert await Namespace.find_one({"prefix": prefix}) is None


@pytest.mark.asyncio
async def test_a_degraded_deletion_is_resumed(client, monkeypatch):
    prefix = f"del-{uuid.uuid4().hex[:8]}"
    journal = DeletionJournal(namespace=prefix, status="completed_with_warnings", steps=[
        DeletionStep(order=1, store="mongodb", collection="registry_entries",
                     filter={"namespace": prefix}, status="completed_with_errors",
                     error="was degraded"),
    ])
    await journal.insert()
    resumed = await _svc(monkeypatch).resume_deletion(prefix)
    assert resumed.status == "completed"
    assert resumed.steps[0].status == "completed" and resumed.steps[0].error is None


@pytest.mark.asyncio
async def test_a_lock_with_no_journal_gets_one_at_resume(client, auth_headers, monkeypatch):
    prefix = await _namespace(client, auth_headers)
    await Namespace.get_motor_collection().update_one({"prefix": prefix}, {"$set": {"status": "locked"}})
    resumed = await _svc(monkeypatch).resume_deletion(prefix)
    assert resumed.status == "completed"
    assert await Namespace.find_one({"prefix": prefix}) is None


@pytest.mark.asyncio
async def test_a_running_journal_is_not_run_twice(client, auth_headers, monkeypatch):
    prefix = f"del-{uuid.uuid4().hex[:8]}"
    journal = DeletionJournal(namespace=prefix, steps=[
        DeletionStep(order=1, store="mongodb", collection="registry_entries",
                     filter={"namespace": prefix}),
    ])
    await journal.insert()
    svc = _svc(monkeypatch)
    svc._running.add(str(journal.id))
    with pytest.raises(mod.DeletionAlreadyRunning):
        await svc.resume_deletion(prefix)
    # The loop counts it as unfinished, so it does not retire under the run.
    assert await svc.recover_incomplete_deletions() >= 1


@pytest.mark.asyncio
async def test_file_keys_are_split_across_steps(client, auth_headers, monkeypatch):
    prefix = await _namespace(client, auth_headers)
    svc = _svc(monkeypatch)
    keys = [f"k{i}" for i in range(2500)]
    monkeypatch.setattr(svc, "_get_storage_keys", AsyncMock(return_value=keys))
    journal = await svc._build_journal(prefix, False, "t", [])
    storage = [s for s in journal.steps if s.store == "file_storage"]
    batches = [await mod.step_storage_keys(s) for s in storage]
    assert [len(b) for b in batches] == [1000, 1000, 500]
    assert [k for b in batches for k in b] == keys


@pytest.mark.asyncio
async def test_re_creation_is_judged_by_namespace_id(client, monkeypatch):
    prefix = f"del-{uuid.uuid4().hex[:8]}"
    svc = _svc(monkeypatch)
    journal = DeletionJournal(namespace=prefix, namespace_id="same-id")
    await Namespace(prefix=prefix, namespace_id="same-id", status="archived").insert()
    assert await svc._namespace_recreated(journal) is False
    await Namespace.get_motor_collection().update_one(
        {"prefix": prefix}, {"$set": {"namespace_id": "another-id", "status": "active"}})
    assert await svc._namespace_recreated(journal) is True


@pytest.mark.asyncio
async def test_a_connection_reset_is_retried_not_recorded_as_degraded(client, monkeypatch):
    svc = _svc(monkeypatch)
    svc._postgres_url = "http://reporting-sync"
    monkeypatch.setattr(mod, "_service_api_key", lambda: "k")
    http = MagicMock()
    http.delete = AsyncMock(side_effect=httpx.ReadError("connection reset"))
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    with patch("httpx.AsyncClient", return_value=http), pytest.raises(mod._Unreachable):
        await svc._exec_postgresql_step(DeletionStep(order=1, store="postgresql"), "x")


@pytest.mark.asyncio
async def test_a_platform_admin_sees_a_locked_namespace(client, auth_headers):
    prefix = await _namespace(client, auth_headers)
    await Namespace.get_motor_collection().update_one({"prefix": prefix}, {"$set": {"status": "locked"}})
    got = await client.get(f"{NS}/{prefix}", headers=auth_headers)
    assert got.status_code == 200 and got.json()["status"] == "locked"
    listed = await client.get(NS, headers=auth_headers)
    assert prefix in [n["prefix"] for n in listed.json()]
    r = await client.post("/api/registry/api-keys", headers=auth_headers, json={
        "name": f"k-{uuid.uuid4().hex[:6]}", "namespaces": [prefix], "grant_permission": "admin",
    })
    scoped = {"X-API-Key": r.json()["plaintext_key"]}
    assert (await client.get(f"{NS}/{prefix}", headers=scoped)).status_code == 404


# ── Reopened (FRanC review of 26d19f9c) ──────────────────────────────────


@pytest.mark.asyncio
async def test_the_keys_live_outside_the_journal(client, auth_headers, monkeypatch):
    """Splitting keys across steps did not shrink the journal: every step
    sits in the one document. The keys now wait in a document per batch, so
    a namespace with 300,000 files builds a journal of a few kilobytes."""
    import bson

    prefix = await _namespace(client, auth_headers)
    svc = _svc(monkeypatch)
    keys = [str(uuid.uuid4()) for _ in range(300_000)]
    monkeypatch.setattr(svc, "_get_storage_keys", AsyncMock(return_value=keys))

    journal = await svc._build_journal(prefix, False, "t", [])

    storage = [s for s in journal.steps if s.store == "file_storage"]
    assert len(storage) == 300 and all(s.storage_keys is None for s in storage)
    size = len(bson.encode(journal.model_dump(mode="json", exclude={"id"})))
    assert size < 1_000_000, size  # the inline keys alone were ~14 MB
    await journal.insert()  # MongoDB takes it
    assert await mod.step_storage_keys(storage[-1]) == keys[-1000:]
    await mod._key_batches().delete_many({"namespace": prefix})


@pytest.mark.asyncio
async def test_a_completed_batch_drops_its_keys(client, auth_headers, monkeypatch):
    prefix = await _namespace(client, auth_headers)
    svc = _svc(monkeypatch)
    monkeypatch.setattr(svc, "_get_storage_keys", AsyncMock(return_value=["k1", "k2"]))
    monkeypatch.setattr(svc, "_exec_file_storage_step", AsyncMock(return_value=2))

    await svc.start_deletion(prefix, requested_by="t")

    assert await mod._key_batches().count_documents({"namespace": prefix}) == 0


@pytest.mark.asyncio
async def test_a_resumed_deletion_leaves_a_namespace_created_again_alone(
    client, auth_headers, monkeypatch,
):
    """A deletion that stopped after the namespace record was gone, resumed
    once another namespace took the prefix, deleted that namespace's data and
    record by name: only the reporting step checked. Every step checks now."""
    prefix = await _namespace(client, auth_headers)
    svc = _svc(monkeypatch)
    templates = Namespace.get_motor_collection().database.client["wip_template_store"]["templates"]
    await templates.insert_one({"namespace": prefix, "template_id": "old"})
    old = await Namespace.find_one({"prefix": prefix})
    journal = await svc._build_journal(prefix, False, "t", [])
    journal.namespace_id = old.namespace_id
    journal.status = "failed"
    await journal.insert()
    assert any(s.collection == "templates" for s in journal.steps)

    # The old namespace's record is gone; a new one holds the prefix, with data.
    await old.delete()
    await templates.delete_many({"namespace": prefix})
    await Namespace(prefix=prefix, namespace_id=f"new-{uuid.uuid4().hex}", status="active").insert()
    await templates.insert_one({"namespace": prefix, "template_id": "new"})
    try:
        resumed = await svc.resume_deletion(prefix)

        assert await templates.count_documents({"namespace": prefix, "template_id": "new"}) == 1
        assert await Namespace.find_one({"prefix": prefix}) is not None
        assert resumed.status == "completed_with_warnings"
        assert all("exists again" in (s.error or "") for s in resumed.steps)
    finally:
        await templates.delete_many({"namespace": prefix})
        await Namespace.find({"prefix": prefix}).delete()
