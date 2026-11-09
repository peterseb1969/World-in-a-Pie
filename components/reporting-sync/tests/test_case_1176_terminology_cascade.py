"""A terminology event carries its terms into PostgreSQL.

def-store deletes and restores a terminology's terms with the terminology
and announces only the terminology, so reporting-sync cascades:

- soft delete: the terminology's ACTIVE terms become inactive (a deprecated
  term stays deprecated, exactly as in def-store);
- restore: its inactive terms become active again — unless the restore was
  made with restore_terms=false;
- hard delete: its terms, their names and every relation touching them are
  removed.

Each marks the namespace's term_closure for rebuild.
"""

from unittest.mock import MagicMock

import pytest

from reporting_sync.models import SyncStatus
from reporting_sync.worker import SyncWorker

from .conftest import requires_postgres

NS = "probe1176"
TID = "0190a000-0000-7000-0000-0000000011a6"
OTHER_TID = "0190a000-0000-7000-0000-0000000011a7"


def _event(event_type, key, payload, **extras):
    return {"event_type": event_type, key: payload, **extras}


def _terminology(status="active", **extra):
    return {
        "terminology_id": TID, "namespace": NS, "value": "PROBE", "label": "Probe",
        "status": status, "term_count": 0, **extra,
    }


def _term(term_id, value, *, terminology_id=TID, status="active"):
    return {
        "term_id": term_id, "namespace": NS, "terminology_id": terminology_id,
        "terminology_value": "PROBE", "value": value, "aliases": [f"{value}-alias"],
        "names": [{"value": f"{value}-alias"}], "status": status,
    }


@pytest.fixture
async def worker(pg_pool):
    w = SyncWorker(MagicMock(), MagicMock(), pg_pool, SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True))
    w.closure.mark_dirty = MagicMock()
    await w._process_terminology_event(_event("terminology.created", "terminology", _terminology()))
    for term in (
        _term("T-A", "a"), _term("T-B", "b"),
        _term("T-DEP", "dep", status="deprecated"),
        _term("T-X", "x", terminology_id=OTHER_TID),
    ):
        await w._process_term_event(_event("term.created", "term", term))
    for source, target in (("T-A", "T-B"), ("T-X", "T-A")):
        await w._process_term_relation_event(_event("term_relation.created", "term_relation", {
            "namespace": NS, "source_term_id": source, "target_term_id": target,
            "relation_type": "is_a",
        }))
    w.closure.mark_dirty.reset_mock()
    return w


async def _statuses(worker):
    terms = await worker.schema_manager.ensure_terms_table(NS)
    async with worker.pool.acquire() as conn:
        rows = await conn.fetch(f'SELECT term_id, status FROM {terms}')  # nosec B608
    return {r["term_id"]: r["status"] for r in rows}


async def _count(worker, ensure):
    table = await ensure(NS)
    async with worker.pool.acquire() as conn:
        return await conn.fetchval(f"SELECT count(*) FROM {table}")  # nosec B608


@requires_postgres
@pytest.mark.asyncio
async def test_soft_delete_deactivates_active_terms_only(worker):
    await worker._process_terminology_event(_event(
        "terminology.deleted", "terminology", _terminology(status="inactive"), changed_by="u",
    ))

    assert await _statuses(worker) == {
        "T-A": "inactive", "T-B": "inactive", "T-DEP": "deprecated", "T-X": "active",
    }
    worker.closure.mark_dirty.assert_called_with(NS)


@requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize("restore_terms", [True, False, None])
async def test_restore_reactivates_inactive_terms(worker, restore_terms):
    await worker._process_terminology_event(_event(
        "terminology.deleted", "terminology", _terminology(status="inactive"),
    ))
    payload = _terminology(status="active")
    if restore_terms is not None:
        payload["restore_terms"] = restore_terms
    await worker._process_terminology_event(_event("terminology.restored", "terminology", payload))

    revived = "inactive" if restore_terms is False else "active"
    assert await _statuses(worker) == {
        "T-A": revived, "T-B": revived, "T-DEP": "deprecated", "T-X": "active",
    }


@requires_postgres
@pytest.mark.asyncio
async def test_hard_delete_removes_terms_names_and_relations(worker):
    assert await _count(worker, worker.schema_manager.ensure_term_names_table) == 4
    assert await _count(worker, worker.schema_manager.ensure_term_relations_table) == 2
    await worker._process_terminology_event(_event(
        "terminology.deleted", "terminology", _terminology(hard_delete=True),
    ))

    assert await _statuses(worker) == {"T-X": "active"}
    names = await worker.schema_manager.ensure_term_names_table(NS)
    async with worker.pool.acquire() as conn:
        left = await conn.fetch(f'SELECT term_id FROM {names}')  # nosec B608
    assert [r["term_id"] for r in left] == ["T-X"]
    # Both relations touched a deleted term — including the one whose source
    # lives in another terminology
    assert await _count(worker, worker.schema_manager.ensure_term_relations_table) == 0
    assert await _count(worker, worker.schema_manager.ensure_terminologies_table) == 0
    worker.closure.mark_dirty.assert_called_with(NS)

