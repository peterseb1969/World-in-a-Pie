"""One open deletion journal per namespace, enforced by the store.

A claim made one journal single-runner, but nothing stopped a second journal
being stored for the same prefix: a resume-delete that landed while a
deletion was still building its journal (or two resumes at once) built and
stored its own and ran a second deletion beside the first. A partial unique
index now keeps one journal per prefix in progress or failed, and every path
that creates or claims a journal treats the store's refusal as "another run
has this deletion".
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

import registry.api.namespace_deletion as api_mod
import registry.services.namespace_deletion as mod
from registry.models.deletion_journal import (
    ONE_OPEN_JOURNAL_INDEX,
    DeletionJournal,
    DeletionStep,
    ensure_one_open_journal_index,
)
from registry.models.namespace import Namespace
from registry.services.namespace_deletion import (
    DeletionAlreadyRunning,
    NamespaceDeletionService,
    _ClaimLost,
)

BASE = "/api/registry/namespaces"


def _svc(monkeypatch) -> NamespaceDeletionService:
    svc = NamespaceDeletionService()
    monkeypatch.setattr(svc, "start_recovery", lambda: None)
    return svc


def _prefix() -> str:
    return f"open-{uuid.uuid4().hex[:8]}"


def _step(prefix: str, order: int = 1, **kw) -> DeletionStep:
    return DeletionStep(
        order=order, store="mongodb", collection="registry_entries",
        filter={"namespace": prefix}, **kw,
    )


async def _namespace(client, headers, prefix: str) -> None:
    r = await client.put(f"{BASE}/{prefix}", headers=headers, json={"deletion_mode": "full"})
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_the_store_holds_one_open_journal_per_namespace(client):
    prefix = _prefix()
    await DeletionJournal(namespace=prefix, status="in_progress").insert()
    for status in ("in_progress", "failed"):
        with pytest.raises(DuplicateKeyError):
            await DeletionJournal(namespace=prefix, status=status).insert()
    # Finished journals are history: any number of them may name the prefix.
    await DeletionJournal(namespace=prefix, status="completed").insert()
    await DeletionJournal(namespace=prefix, status="completed_with_warnings").insert()
    assert await DeletionJournal.find({"namespace": prefix}).count() == 3


@pytest.mark.asyncio
async def test_a_resume_that_loses_the_build_race_stores_and_runs_nothing(
    client, auth_headers, monkeypatch,
):
    """A namespace locked with no journal yet (its deletion is building
    one): a resume builds its own, but the deletion stores first. The
    resume's journal is refused, its key batches are dropped, and it runs
    nothing."""
    prefix = _prefix()
    await _namespace(client, auth_headers, prefix)
    await Namespace.find_one({"prefix": prefix}).update({"$set": {"status": "locked"}})
    svc = _svc(monkeypatch)
    dropped: list[list[str]] = []
    real_drop = mod._drop_batches

    async def drop(batches):
        dropped.append(list(batches))
        await real_drop(batches)

    loser_batch = str(ObjectId())

    async def build(prefix_, force, requested_by, inbound_refs, batches):
        batches.append(loser_batch)
        # The deletion stores its journal while this resume builds.
        await DeletionJournal(
            namespace=prefix_, status="in_progress",
            run_token="the-deletion", run_heartbeat=datetime.now(UTC),
        ).insert()
        return DeletionJournal(namespace=prefix_, steps=[_step(prefix_)])

    monkeypatch.setattr(svc, "_check_inbound_references", lambda p: asyncio.sleep(0, []))
    monkeypatch.setattr(svc, "_build_journal", build)
    monkeypatch.setattr(mod, "_drop_batches", drop)
    ran: list[str] = []
    monkeypatch.setattr(svc, "_execute_journal", lambda j: ran.append(str(j.id)))

    with pytest.raises(DeletionAlreadyRunning):
        await svc.resume_deletion(prefix)
    assert ran == []
    assert dropped == [[loser_batch]]
    [only] = await DeletionJournal.find({"namespace": prefix}).to_list()
    assert only.run_token == "the-deletion"


@pytest.mark.asyncio
async def test_a_deletion_meeting_an_unfinished_earlier_one_gives_the_namespace_back(
    client, auth_headers,
):
    """An earlier deletion of the name stopped on a failed step (a namespace
    created again before the re-create gate existed): a new deletion's
    journal is refused, so it deletes nothing, unlocks the namespace and
    names resume-delete."""
    prefix = _prefix()
    await _namespace(client, auth_headers, prefix)
    await DeletionJournal(
        namespace=prefix, status="failed", steps=[_step(prefix, status="failed", error="x")],
    ).insert()

    r = await client.delete(f"{BASE}/{prefix}", headers=auth_headers)
    assert r.status_code == 409, r.text
    assert "resume-delete" in r.json()["detail"]
    ns = await Namespace.find_one({"prefix": prefix})
    assert ns is not None and ns.status == "active"
    assert await DeletionJournal.find({"namespace": prefix}).count() == 1


@pytest.mark.asyncio
async def test_a_claim_the_store_refuses_is_not_taken(client, monkeypatch):
    """Resuming a degraded deletion sets it in progress, which the store
    refuses while another journal of the prefix is open: not claimed, and
    answered as running."""
    prefix = _prefix()
    await DeletionJournal(namespace=prefix, status="failed",
                          requested_at=datetime.now(UTC) - timedelta(hours=1)).insert()
    newer = DeletionJournal(namespace=prefix, status="completed_with_warnings",
                            steps=[_step(prefix, status="completed_with_errors")])
    await newer.insert()

    with pytest.raises(DeletionAlreadyRunning):
        await _svc(monkeypatch).resume_deletion(prefix)
    stored = await DeletionJournal.get(newer.id)
    assert stored.status == "completed_with_warnings" and stored.run_token is None


@pytest.mark.asyncio
async def test_a_deletion_that_finished_meanwhile_is_not_reported_running(client):
    prefix = _prefix()
    journal = DeletionJournal(namespace=prefix, status="completed")
    await journal.insert()
    answer = await mod._why_not_claimed(journal)
    assert isinstance(answer, ValueError) and "finished" in str(answer)


@pytest.mark.asyncio
async def test_a_resume_never_claims_a_journal_this_process_runs(client, monkeypatch):
    prefix = _prefix()
    journal = DeletionJournal(namespace=prefix, status="in_progress", steps=[_step(prefix)])
    await journal.insert()
    svc = _svc(monkeypatch)
    svc._running.add(str(journal.id))

    with pytest.raises(DeletionAlreadyRunning):
        await svc.resume_deletion(prefix)
    stored = await DeletionJournal.get(journal.id)
    assert stored.run_token is None


@pytest.mark.asyncio
async def test_a_long_step_keeps_its_claim(client, monkeypatch):
    prefix = _prefix()
    journal = DeletionJournal(namespace=prefix, status="in_progress", steps=[_step(prefix)])
    await journal.insert()
    svc = _svc(monkeypatch)
    monkeypatch.setattr(mod, "_HEARTBEAT_INTERVAL", timedelta(milliseconds=40))
    seen: list[datetime] = []

    async def long_step(step, namespace):
        for _ in range(2):
            await asyncio.sleep(0.15)
            stored = await DeletionJournal.get_motor_collection().find_one({"_id": journal.id})
            seen.append(stored["run_heartbeat"])
        return 1

    monkeypatch.setattr(svc, "_execute_step", long_step)
    await svc.resume_deletion(prefix)
    # The heartbeat moved during the one step, with no journal write between.
    assert len(seen) == 2 and seen[1] > seen[0]


@pytest.mark.asyncio
async def test_a_deletion_taken_over_answers_409(client, auth_headers, monkeypatch):
    prefix = _prefix()
    await _namespace(client, auth_headers, prefix)

    async def taken_over(**kwargs):
        raise _ClaimLost(f"The deletion of namespace '{prefix}' was taken over by another run")

    monkeypatch.setattr(api_mod._deletion_service, "start_deletion", taken_over)
    r = await client.delete(f"{BASE}/{prefix}", headers=auth_headers)
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_existing_duplicates_leave_the_index_unbuilt_and_are_logged(client, caplog):
    collection = DeletionJournal.get_motor_collection()
    name = ONE_OPEN_JOURNAL_INDEX.document["name"]
    await collection.drop_index(name)
    prefix = _prefix()
    try:
        await DeletionJournal(namespace=prefix, status="in_progress").insert()
        await DeletionJournal(namespace=prefix, status="failed").insert()
        with caplog.at_level(logging.CRITICAL, logger="registry.startup"):
            groups = await ensure_one_open_journal_index()
        assert [g["_id"] for g in groups] == [prefix]
        assert any(name in r.getMessage() for r in caplog.records)
        assert name not in await collection.index_information()
    finally:
        await DeletionJournal.find({"namespace": prefix}).delete()
        assert await ensure_one_open_journal_index() == []
    assert name in await collection.index_information()
