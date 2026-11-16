"""Namespace deletion: every lock has a journal behind it.

The lock used to be taken before the journal was stored. A process that died
in between (killed, out of memory, rolled by a redeploy while the storage keys
were being batched) left a namespace locked with no journal: deletion-status
answered 404, a new delete 409, and the recovery loop, which reads journals,
never saw it. Only a manual resume-delete healed it.

The journal is now stored first, claimed by the run and marked building, then
the namespace is locked, then the steps are stored. A run that dies anywhere
in that window leaves an open journal; once its claim's lease ages out the
recovery loop takes it over: it builds the steps and finishes the deletion the
caller asked for when the namespace is locked, and discards the journal,
deleting nothing, when it is not.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pymongo.errors import OperationFailure

import registry.services.namespace_deletion as mod
from registry.models.deletion_journal import DeletionJournal
from registry.models.namespace import Namespace
from registry.services.namespace_deletion import DeletionNotStarted, NamespaceDeletionService

NS = "/api/registry/namespaces"


def _svc(monkeypatch) -> NamespaceDeletionService:
    svc = NamespaceDeletionService()
    monkeypatch.setattr(svc, "start_recovery", lambda: None)
    return svc


async def _namespace(client, headers) -> Namespace:
    prefix = f"jbl-{uuid.uuid4().hex[:8]}"
    r = await client.put(f"{NS}/{prefix}", json={"deletion_mode": "full"}, headers=headers)
    assert r.status_code == 200, r.text
    ns = await Namespace.find_one({"prefix": prefix})
    assert ns is not None
    return ns


async def _abandoned_journal(ns: Namespace, *, force: bool = False) -> DeletionJournal:
    """The journal a run left when it died before storing the steps: building,
    claimed by a run whose heartbeat is older than the lease."""
    journal = DeletionJournal(
        namespace=ns.prefix, namespace_id=ns.namespace_id, building=True, force=force,
        requested_by="apikey:the-caller", run_token="dead-run",
        run_heartbeat=datetime.now(UTC) - mod._CLAIM_LEASE - timedelta(minutes=1),
    )
    await journal.insert()
    return journal


async def _lock(ns: Namespace) -> None:
    await Namespace.get_motor_collection().update_one(
        {"prefix": ns.prefix}, {"$set": {"status": "locked"}},
    )


@pytest.mark.asyncio
async def test_the_journal_is_stored_before_the_namespace_is_locked(
    client, auth_headers, monkeypatch,
):
    ns = await _namespace(client, auth_headers)
    svc = _svc(monkeypatch)
    seen: list[DeletionJournal | None] = []
    lock = mod.set_unless_locked

    async def lock_and_look(namespace, fields):
        seen.append(await DeletionJournal.find_one({"namespace": ns.prefix}))
        return await lock(namespace, fields)

    monkeypatch.setattr(mod, "set_unless_locked", lock_and_look)
    journal = await svc.start_deletion(ns.prefix, requested_by="apikey:the-caller")

    [at_lock] = seen
    assert at_lock is not None and at_lock.building and at_lock.run_token, at_lock
    assert at_lock.steps == []
    assert journal.status == "completed" and not journal.building
    assert await Namespace.find_one({"prefix": ns.prefix}) is None


@pytest.mark.asyncio
async def test_recovery_finishes_a_deletion_whose_run_died_after_the_lock(
    client, auth_headers, monkeypatch,
):
    ns = await _namespace(client, auth_headers)
    await _lock(ns)
    abandoned = await _abandoned_journal(ns, force=True)
    svc = _svc(monkeypatch)

    assert await svc.recover_incomplete_deletions() == 0

    stored = await DeletionJournal.get(abandoned.id)
    assert stored is not None and stored.status == "completed", stored
    assert not stored.building and stored.steps
    assert stored.requested_by == "apikey:the-caller" and stored.force is True
    assert await Namespace.find_one({"prefix": ns.prefix}) is None


@pytest.mark.asyncio
async def test_recovery_discards_a_journal_whose_run_died_before_the_lock(
    client, auth_headers, monkeypatch,
):
    ns = await _namespace(client, auth_headers)
    abandoned = await _abandoned_journal(ns)
    svc = _svc(monkeypatch)

    assert await svc.recover_incomplete_deletions() == 0

    assert await DeletionJournal.get(abandoned.id) is None
    kept = await Namespace.find_one({"prefix": ns.prefix})
    assert kept is not None and kept.status == "active"


@pytest.mark.asyncio
async def test_a_live_run_still_building_is_not_taken_over(client, auth_headers, monkeypatch):
    ns = await _namespace(client, auth_headers)
    await _lock(ns)
    journal = await _abandoned_journal(ns)
    await DeletionJournal.get_motor_collection().update_one(
        {"_id": journal.id}, {"$set": {"run_heartbeat": datetime.now(UTC)}},
    )
    svc = _svc(monkeypatch)

    assert await svc.recover_incomplete_deletions() == 1
    stored = await DeletionJournal.get(journal.id)
    assert stored is not None and stored.building and stored.run_token == "dead-run"


@pytest.mark.asyncio
async def test_resuming_a_journal_that_never_locked_answers_not_found(client, auth_headers):
    ns = await _namespace(client, auth_headers)
    await _abandoned_journal(ns)

    r = await client.post(f"{NS}/{ns.prefix}/resume-delete", headers=auth_headers)

    assert r.status_code == 404, r.text
    assert "deleted nothing" in r.json()["detail"]
    assert await DeletionJournal.find_one({"namespace": ns.prefix}) is None
    assert (await Namespace.find_one({"prefix": ns.prefix})).status == "active"


@pytest.mark.asyncio
async def test_a_failure_storing_the_steps_gives_the_namespace_back(
    client, auth_headers, monkeypatch,
):
    ns = await _namespace(client, auth_headers)
    svc = _svc(monkeypatch)

    async def fail(*_a, **_k):
        raise OperationFailure("not primary", code=10107)

    monkeypatch.setattr(mod, "_write_journal", fail)
    with pytest.raises(DeletionNotStarted, match="is active again"):
        await svc.start_deletion(ns.prefix, requested_by="t")

    assert (await Namespace.find_one({"prefix": ns.prefix})).status == "active"
    assert await DeletionJournal.find_one({"namespace": ns.prefix}) is None
    assert await mod._key_batches().count_documents({"namespace": ns.prefix}) == 0


@pytest.mark.asyncio
async def test_a_namespace_that_cannot_be_given_back_keeps_its_journal(
    client, auth_headers, monkeypatch,
):
    """The undo itself fails (the same database fault): the answer says the
    namespace stays locked, and the journal stays, so the deletion is finished
    later instead of the namespace staying locked with nothing behind it."""
    ns = await _namespace(client, auth_headers)
    svc = _svc(monkeypatch)

    async def fail(*_a, **_k):
        raise OperationFailure("not primary", code=10107)

    monkeypatch.setattr(mod, "_write_journal", fail)
    monkeypatch.setattr(mod, "_unlock", fail)
    with pytest.raises(DeletionNotStarted, match="stays locked"):
        await svc.start_deletion(ns.prefix, requested_by="t")

    assert (await Namespace.find_one({"prefix": ns.prefix})).status == "locked"
    kept = await DeletionJournal.find_one({"namespace": ns.prefix})
    assert kept is not None and kept.building and kept.status == "in_progress"
