"""Namespace deletion: what the review of the journal-size fix found.

- A deletion that cannot store its journal (a database error writing the key
  batches or the journal) answered 500 and left the namespace locked with no
  journal. Nothing has been deleted then: the namespace is unlocked again,
  the batches written are dropped, and the answer is 503. A resume that
  cannot build the journal of a namespace locked with none drops its batches,
  stays locked and answers 503.
- The journal kept every broken inbound reference, unbounded, in its one
  document: it keeps the first 1,000, counts every one per kind and source
  namespace, and says it was cut.
- Key batches stayed forever when their step was left undone or the journal
  was never stored: a step left undone drops its batch, and a startup sweep
  drops batches no step still needs.
- A file-storage step naming no keys at all deleted nothing and read as a
  clean step: it is degraded.
- A degraded step run again counted only its last run, and the summary left
  out steps left undone: a step's count is what all its runs deleted, and
  the summary counts every step that ran.
- resume-delete built a missing journal past high-severity inbound references
  and recorded "resume-delete" as the requester: it needs force, as a
  deletion does, and records the caller; its 400/404/409/503 are declared.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId
from pymongo.errors import OperationFailure

import registry.services.namespace_deletion as mod
from registry.models.deletion_journal import DeletionJournal, DeletionStep, InboundReference
from registry.models.namespace import Namespace
from registry.services.namespace_deletion import (
    DeletionNotStarted,
    DeletionRefused,
    NamespaceDeletionService,
)

NS = "/api/registry/namespaces"


def _svc(monkeypatch) -> NamespaceDeletionService:
    svc = NamespaceDeletionService()
    monkeypatch.setattr(svc, "start_recovery", lambda: None)
    return svc


def _streamed(keys):
    async def stream(_prefix):
        for k in keys:
            yield k
    return stream


async def _namespace(client, headers) -> str:
    prefix = f"rv-{uuid.uuid4().hex[:8]}"
    r = await client.put(f"{NS}/{prefix}", json={"deletion_mode": "full"}, headers=headers)
    assert r.status_code == 200, r.text
    return prefix


async def _batches_of(prefix: str) -> int:
    return await mod._key_batches().count_documents({"namespace": prefix})


def _failing_create(monkeypatch) -> None:
    async def create(self, *a, **k):
        raise OperationFailure("not primary", code=10107)
    monkeypatch.setattr(DeletionJournal, "create", create)


def _ref(kind: str, ns: str, n: int) -> InboundReference:
    return InboundReference(
        type=kind, source_namespace=ns, source_entity=f"e{n}", target_entity=f"t{n}",
        impact="a reference is broken",
    )


@pytest.mark.asyncio
async def test_a_deletion_that_cannot_store_its_journal_unlocks_the_namespace(
    client, auth_headers, monkeypatch,
):
    prefix = await _namespace(client, auth_headers)
    svc = _svc(monkeypatch)
    monkeypatch.setattr(svc, "_iter_storage_keys", _streamed(["k1", "k2"]))
    _failing_create(monkeypatch)

    with pytest.raises(DeletionNotStarted, match="Nothing was deleted"):
        await svc.start_deletion(prefix, requested_by="t")
    assert (await Namespace.find_one({"prefix": prefix})).status == "active"
    assert await _batches_of(prefix) == 0
    assert await DeletionJournal.find_one({"namespace": prefix}) is None


@pytest.mark.asyncio
async def test_the_delete_route_answers_503_and_the_namespace_is_usable(
    client, auth_headers, monkeypatch,
):
    prefix = await _namespace(client, auth_headers)
    _failing_create(monkeypatch)
    r = await client.delete(f"{NS}/{prefix}", headers=auth_headers)
    assert r.status_code == 503, r.text
    monkeypatch.undo()
    r = await client.get(f"{NS}/{prefix}", headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "active"


@pytest.mark.asyncio
async def test_a_resume_that_cannot_build_the_journal_stays_locked(
    client, auth_headers, monkeypatch,
):
    prefix = await _namespace(client, auth_headers)
    await Namespace.get_motor_collection().update_one(
        {"prefix": prefix}, {"$set": {"status": "locked"}},
    )
    svc = _svc(monkeypatch)
    monkeypatch.setattr(svc, "_iter_storage_keys", _streamed(["k1"]))
    _failing_create(monkeypatch)

    with pytest.raises(DeletionNotStarted, match="stays locked"):
        await svc.resume_deletion(prefix)
    assert (await Namespace.find_one({"prefix": prefix})).status == "locked"
    assert await _batches_of(prefix) == 0


@pytest.mark.asyncio
async def test_a_resume_building_a_journal_needs_force_and_records_the_caller(
    client, auth_headers, monkeypatch,
):
    prefix = await _namespace(client, auth_headers)
    await Namespace.get_motor_collection().update_one(
        {"prefix": prefix}, {"$set": {"status": "locked"}},
    )
    svc = _svc(monkeypatch)
    high = next(iter(mod._HIGH_SEVERITY_INBOUND))

    async def refs(_prefix):
        return [_ref(high, "elsewhere", 1)]

    monkeypatch.setattr(svc, "_check_inbound_references", refs)
    with pytest.raises(DeletionRefused, match="force"):
        await svc.resume_deletion(prefix, requested_by="apikey:caller")
    assert await DeletionJournal.find_one({"namespace": prefix}) is None

    journal = await svc.resume_deletion(prefix, force=True, requested_by="apikey:caller")
    assert journal.status == "completed"
    assert journal.requested_by == "apikey:caller" and journal.force is True


@pytest.mark.asyncio
async def test_the_journal_keeps_the_first_references_and_counts_them_all(client, monkeypatch):
    refs = [_ref("synonym_link", "a", i) for i in range(900)]
    refs += [_ref("synonym_link", "b", i) for i in range(600)]
    svc = _svc(monkeypatch)
    monkeypatch.setattr(svc, "_iter_storage_keys", _streamed([]))
    journal = await svc._build_journal(f"rv-{uuid.uuid4().hex[:8]}", True, None, refs)

    assert len(journal.broken_references) == mod._BROKEN_REFS_KEPT
    assert journal.broken_references_truncated is True
    counts = {(c.type, c.source_namespace): c.count for c in journal.broken_references_counts}
    assert counts == {("synonym_link", "a"): 900, ("synonym_link", "b"): 600}


@pytest.mark.asyncio
async def test_a_step_left_undone_drops_its_batch(client, auth_headers, monkeypatch):
    prefix = await _namespace(client, auth_headers)
    batch = await mod._store_key_batch(prefix, ["k1"])
    journal = DeletionJournal(
        namespace=prefix, namespace_id="the-deleted-one", status="in_progress",
        steps=[DeletionStep(order=1, store="file_storage", storage_keys_batch=batch)],
    )
    await journal.insert()
    # The prefix is held by another namespace now (its own id).
    await _svc(monkeypatch).resume_deletion(prefix)
    stored = await DeletionJournal.get(journal.id)
    assert stored.steps[0].status == "completed_with_errors"
    assert await mod._key_batches().find_one({"_id": ObjectId(batch)}) is None


@pytest.mark.asyncio
async def test_the_sweep_drops_only_batches_no_step_needs(client):
    prefix = f"rv-{uuid.uuid4().hex[:8]}"
    coll = mod._key_batches()
    old = datetime.now(UTC) - timedelta(hours=2)
    orphan = (await coll.insert_one({"namespace": prefix, "keys": ["a"], "created_at": old})).inserted_id
    young = (await coll.insert_one({
        "namespace": prefix, "keys": ["b"], "created_at": datetime.now(UTC),
    })).inserted_id
    needed = (await coll.insert_one({"namespace": prefix, "keys": ["c"], "created_at": old})).inserted_id
    done = (await coll.insert_one({"namespace": prefix, "keys": ["d"], "created_at": old})).inserted_id
    await DeletionJournal(namespace=prefix, status="failed", steps=[
        DeletionStep(order=1, store="file_storage", storage_keys_batch=str(needed), status="pending"),
        DeletionStep(order=2, store="file_storage", storage_keys_batch=str(done), status="completed"),
    ]).insert()

    await mod.sweep_key_batches()
    left = {d["_id"] async for d in coll.find({"namespace": prefix})}
    assert left == {young, needed}
    assert orphan not in left and done not in left
    assert "namespace_idx" in await coll.index_information()


@pytest.mark.asyncio
async def test_a_file_step_naming_no_keys_is_degraded(client, monkeypatch):
    svc = _svc(monkeypatch)
    step = DeletionStep(order=1, store="file_storage")
    assert await svc._exec_file_storage_step(step, "any") == 0
    assert step.error and "may remain" in step.error


@pytest.mark.asyncio
async def test_counts_add_up_across_runs_and_include_steps_left_undone(client, monkeypatch):
    prefix = f"rv-{uuid.uuid4().hex[:8]}"
    journal = DeletionJournal(namespace=prefix, status="completed_with_warnings", steps=[
        DeletionStep(order=1, store="file_storage", storage_keys=["k"] * 1000,
                     status="completed_with_errors", deleted_count=600, error="400 refused"),
        DeletionStep(order=2, store="mongodb", collection="registry_entries",
                     filter={"namespace": prefix}),
    ])
    await journal.insert()
    svc = _svc(monkeypatch)

    async def execute(step, namespace):
        return 400 if step.store == "file_storage" else 5

    monkeypatch.setattr(svc, "_execute_step", execute)
    resumed = await svc.resume_deletion(prefix)
    assert resumed.steps[0].deleted_count == 1000
    assert resumed.summary == {"minio_objects": 1000, "registry_entries": 5}


@pytest.mark.asyncio
async def test_resume_delete_declares_its_answers(client):
    r = await client.get("/openapi.json")
    paths = r.json()["paths"]
    resume = next(v for k, v in paths.items() if k.endswith("/resume-delete"))["post"]
    assert {"400", "404", "409", "503"} <= set(resume["responses"])
    assert any(p["name"] == "force" for p in resume["parameters"])
