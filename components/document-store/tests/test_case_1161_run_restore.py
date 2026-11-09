"""A plain restore's batching and skips, pinned before its refactor.

Provenance: CASE-1161. run_restore's mid-loop batch flush (a batch filling
before the archive's rows run out), the registry-key claims made per batch,
and the skip_documents / skip_files branches had no test. These pin what
they do against the real test MongoDB, so restructuring the method cannot
change it unnoticed.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.backup_engine import COLLECTION_MAP, DirectRestoreEngine
from tests.test_merge_restore import NAMESPACE, _all_collections, _archive

ROWS = 5


@pytest_asyncio.fixture
async def mongo(session_mongo_client):
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        for db_name, coll_name in _all_collections():
            key = "prefix" if coll_name == "namespaces" else "namespace"
            await client[db_name][coll_name].delete_many({key: NAMESPACE})

    await _clear()
    yield client
    await _clear()
    client.close()


def _rows() -> dict[str, list[dict]]:
    return {
        "terminologies": [
            {"terminology_id": f"T{i}", "namespace": NAMESPACE, "value": f"V{i}", "status": "active"}
            for i in range(ROWS)
        ],
        "registry_entries": [
            {"entry_id": f"T{i}", "namespace": NAMESPACE, "entity_type": "terminologies"}
            for i in range(ROWS)
        ],
        "documents": [{"document_id": "D1", "namespace": NAMESPACE, "version": 1}],
        "files": [{"file_id": "F1", "namespace": NAMESPACE}],
    }


def _reader(rows: dict[str, list[dict]]):
    reader = _archive(rows)
    reader.entity_count = MagicMock(
        side_effect=lambda entity_type, namespace="": len(rows.get(entity_type, []))
    )
    return reader


async def _restore(mongo, rows, **kwargs):
    events: list = []
    engine = DirectRestoreEngine(mongo, None, events.append)
    engine._upsert_namespace = AsyncMock()
    engine._claim_inserted_entries = AsyncMock(side_effect=lambda ns, batch: (len(batch), 0))
    engine._report_claims = MagicMock()
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=_reader(rows)):
        await engine.run_restore(MagicMock(), NAMESPACE, **kwargs)
    return engine, events


async def _ids(mongo, entity_type: str, key: str) -> list[str]:
    db_name, coll_name = COLLECTION_MAP[entity_type]
    rows = await mongo[db_name][coll_name].find({"namespace": NAMESPACE}).to_list(None)
    return sorted(r[key] for r in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("batch_size", [2, 500])
async def test_every_row_is_restored_whatever_the_batch_size(mongo, batch_size):
    engine, _events = await _restore(mongo, _rows(), batch_size=batch_size)
    assert await _ids(mongo, "terminologies", "terminology_id") == [f"T{i}" for i in range(ROWS)]
    assert await _ids(mongo, "registry_entries", "entry_id") == [f"T{i}" for i in range(ROWS)]
    assert await _ids(mongo, "documents", "document_id") == ["D1"]
    assert await _ids(mongo, "files", "file_id") == ["F1"]
    # The registry keys are claimed batch by batch, and reported once in total.
    sizes = [len(call.args[1]) for call in engine._claim_inserted_entries.await_args_list]
    assert sizes == ([2, 2, 1] if batch_size == 2 else [ROWS])
    engine._report_claims.assert_called_once_with(NAMESPACE, ROWS, 0)


@pytest.mark.asyncio
async def test_progress_counts_every_restored_row_once(mongo):
    _engine, events = await _restore(mongo, _rows(), batch_size=2)
    complete = [e for e in events if e.phase == "complete"]
    assert len(complete) == 1 and complete[0].percent == 100
    phases = [e.phase for e in events]
    assert phases.index("phase_terminologies") < phases.index("phase_registry_entries")


@pytest.mark.asyncio
async def test_skip_documents_and_skip_files_restore_neither(mongo):
    await _restore(mongo, _rows(), skip_documents=True, skip_files=True)
    assert await _ids(mongo, "documents", "document_id") == []
    assert await _ids(mongo, "files", "file_id") == []
    assert len(await _ids(mongo, "terminologies", "terminology_id")) == ROWS


def _rows_with_entries() -> dict[str, list[dict]]:
    rows = _rows()
    rows["registry_entries"] = rows["registry_entries"] + [
        {"entry_id": "D1", "namespace": NAMESPACE, "entity_type": "documents"},
        {"entry_id": "F1", "namespace": NAMESPACE, "entity_type": "files"},
    ]
    return rows


@pytest.mark.asyncio
@pytest.mark.parametrize("skip", ["skip_documents", "skip_files"])
async def test_a_skipped_entity_leaves_its_registry_entry_out(mongo, skip):
    """An entry claims its entity's keys; restored without the entity it
    would hold them for nothing (CASE-1194, 2-R6)."""
    engine, _events = await _restore(mongo, _rows_with_entries(), **{skip: True})
    entries = await _ids(mongo, "registry_entries", "entry_id")
    left_out, kept = ("D1", "F1") if skip == "skip_documents" else ("F1", "D1")
    assert left_out not in entries and kept in entries
    claimed = [r["entry_id"] for call in engine._claim_inserted_entries.await_args_list for r in call.args[1]]
    assert left_out not in claimed


@pytest.mark.asyncio
@pytest.mark.parametrize("skip", ["skip_documents", "skip_files"])
async def test_each_skip_leaves_out_only_its_own_rows(mongo, skip):
    await _restore(mongo, _rows(), **{skip: True})
    documents = await _ids(mongo, "documents", "document_id")
    files = await _ids(mongo, "files", "file_id")
    assert (documents, files) == (([], ["F1"]) if skip == "skip_documents" else (["D1"], []))
