"""One deletion journal is run by one run at a time, from a fresh read.

The only guard against a second run of a journal was an in-process set:
another Registry process, or the recovery loop holding a copy it read before
a request finished the journal, ran it again from its stale copy, re-ran
finished steps and wrote zero counts over the real ones. A run now claims
the journal with one conditional update (no live claim, a status it may run
from) and works from the copy that update returns; every write it makes
sets only the fields it changes, and only while it still holds the claim.
A claim whose run stopped writing for longer than the lease is taken over.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

import registry.services.namespace_deletion as mod
from registry.models.deletion_journal import DeletionJournal, DeletionStep
from registry.services.namespace_deletion import DeletionAlreadyRunning, NamespaceDeletionService


def _svc(monkeypatch) -> NamespaceDeletionService:
    svc = NamespaceDeletionService()
    monkeypatch.setattr(svc, "start_recovery", lambda: None)
    return svc


def _prefix() -> str:
    return f"claim-{uuid.uuid4().hex[:8]}"


def _step(prefix: str, order: int, **kw) -> DeletionStep:
    return DeletionStep(
        order=order, store="mongodb", collection="registry_entries",
        filter={"namespace": prefix}, **kw,
    )


@pytest.mark.asyncio
async def test_a_resume_while_another_run_holds_the_journal_writes_nothing(client, monkeypatch):
    prefix = _prefix()
    journal = DeletionJournal(
        namespace=prefix, status="failed", steps=[_step(prefix, 1, status="failed", error="x")],
        run_token="another-run", run_heartbeat=datetime.now(UTC),
    )
    await journal.insert()

    with pytest.raises(DeletionAlreadyRunning):
        await _svc(monkeypatch).resume_deletion(prefix)
    stored = await DeletionJournal.get(journal.id)
    assert stored.status == "failed" and stored.steps[0].error == "x"
    assert stored.run_token == "another-run"


@pytest.mark.asyncio
async def test_a_claim_whose_run_stopped_is_taken_over(client, monkeypatch):
    prefix = _prefix()
    journal = DeletionJournal(
        namespace=prefix, status="in_progress", steps=[_step(prefix, 1)],
        run_token="a-dead-run", run_heartbeat=datetime.now(UTC) - timedelta(hours=1),
    )
    await journal.insert()

    resumed = await _svc(monkeypatch).resume_deletion(prefix)
    assert resumed.status == "completed"
    stored = await DeletionJournal.get(journal.id)
    assert stored.status == "completed" and stored.run_token is None


@pytest.mark.asyncio
async def test_the_loop_does_not_run_a_stale_copy_of_a_finished_journal(client, monkeypatch):
    prefix = _prefix()
    journal = DeletionJournal(namespace=prefix, status="in_progress", steps=[_step(prefix, 1)])
    await journal.insert()
    stale = await DeletionJournal.get(journal.id)  # read while still in progress

    # A request finishes the journal; its step deleted 7 entries.
    await DeletionJournal.get_motor_collection().update_one({"_id": journal.id}, {"$set": {
        "status": "completed", "steps.0.status": "completed", "steps.0.deleted_count": 7,
        "summary": {"registry_entries": 7},
    }})

    class _Find:
        def __init__(self, *_a, **_k):
            pass

        async def to_list(self):
            return [stale]

    svc = _svc(monkeypatch)
    ran = []
    monkeypatch.setattr(svc, "_execute_step", lambda *a: ran.append(a))
    monkeypatch.setattr(DeletionJournal, "find", _Find)
    assert await svc.recover_incomplete_deletions() == 0

    assert ran == []
    stored = await DeletionJournal.get_motor_collection().find_one({"_id": journal.id})
    assert stored["status"] == "completed"
    assert stored["steps"][0]["deleted_count"] == 7
    assert stored["summary"] == {"registry_entries": 7}


@pytest.mark.asyncio
async def test_a_run_that_lost_its_claim_stops_without_writing(client, monkeypatch):
    prefix = _prefix()
    journal = DeletionJournal(
        namespace=prefix, status="in_progress", steps=[_step(prefix, 1), _step(prefix, 2)],
    )
    await journal.insert()
    svc = _svc(monkeypatch)

    async def taken_over_meanwhile(step, namespace):
        # Another run takes the journal over while this step runs.
        await DeletionJournal.get_motor_collection().update_one(
            {"_id": journal.id},
            {"$set": {"run_token": "the-other-run", "run_heartbeat": datetime.now(UTC)}},
        )
        return 3

    monkeypatch.setattr(svc, "_execute_step", taken_over_meanwhile)
    with pytest.raises(DeletionAlreadyRunning):
        await svc.resume_deletion(prefix)

    stored = await DeletionJournal.get(journal.id)
    assert [s.status for s in stored.steps] == ["pending", "pending"]
    assert stored.run_token == "the-other-run"


@pytest.mark.asyncio
async def test_writes_set_only_the_fields_of_the_step_they_record(client, monkeypatch):
    prefix = _prefix()
    journal = DeletionJournal(namespace=prefix, status="in_progress", steps=[_step(prefix, 1)])
    await journal.insert()
    svc = _svc(monkeypatch)

    async def note_meanwhile(step, namespace):
        # Another writer records something on the journal while the step runs
        # (the requester's note): the run's write must not erase it.
        await DeletionJournal.get_motor_collection().update_one(
            {"_id": journal.id}, {"$set": {"requested_note": "kept"}},
        )
        return 2

    monkeypatch.setattr(svc, "_execute_step", note_meanwhile)
    await svc.resume_deletion(prefix)
    stored = await DeletionJournal.get(journal.id)
    assert stored.requested_note == "kept"
    assert stored.steps[0].status == "completed" and stored.steps[0].deleted_count == 2
    assert stored.status == "completed"


def test_the_lease_is_longer_than_a_step_writes_apart():
    # A run refreshes its claim with every write; one step (a batch of 1,000
    # stored objects, a delete_many) must fit inside the lease many times over.
    assert mod._CLAIM_LEASE >= timedelta(minutes=10)
