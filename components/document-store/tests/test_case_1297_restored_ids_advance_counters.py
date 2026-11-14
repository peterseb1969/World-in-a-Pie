"""A restore has the Registry account for every entry ID it writes.

The ID-preserving restore and a merge write the Registry entries an archive
carries straight to MongoDB, so the Registry never saw their IDs. A Registry
process reads a prefix's highest existing ID once, the first time it mints
from it, and only raises the counter after that for IDs it is given: one that
had minted from a prefix before the restore minted the restored IDs again.
Now each batch of entries is sent to /entries/advance-counters before it is
written, and a Registry that does not answer stops the write.
"""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.backup_engine import (
    COLLECTION_MAP,
    DirectRestoreEngine,
    RestoreEngineError,
)
from tests.test_merge_restore import NAMESPACE, _all_collections, _archive

ENTRIES = [
    {"entry_id": f"P1297-{i:06d}", "namespace": NAMESPACE, "entity_type": "terminologies"}
    for i in range(1, 6)
]


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


def _entries_collection(mongo):
    db_name, coll_name = COLLECTION_MAP["registry_entries"]
    return mongo[db_name][coll_name]


def _registry(monkeypatch, handler):
    """Route the engine's httpx calls to ``handler``."""
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda *a, **kw: real(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )


def _engine(mongo):
    engine = DirectRestoreEngine(mongo, None, lambda _e: None, registry_base_url="http://registry")
    engine._upsert_namespace = AsyncMock()
    engine._claim_inserted_entries = AsyncMock(side_effect=lambda ns, batch: (len(batch), 0))
    engine._report_claims = MagicMock()
    return engine


async def _restore(mongo, rows):
    reader = _archive(rows)
    reader.entity_count = MagicMock(
        side_effect=lambda entity_type, namespace="": len(rows.get(entity_type, []))
    )
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=reader):
        await _engine(mongo).run_restore(MagicMock(), NAMESPACE, batch_size=2)


@pytest.mark.asyncio
async def test_a_restore_advances_every_entry_id_before_writing_it(mongo, monkeypatch):
    sent: list[str] = []
    written_when_sent: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/registry/entries/advance-counters"
        batch = [item["entry_id"] for item in json.loads(request.content)]
        written_when_sent.append(await _entries_collection(mongo).count_documents(
            {"entry_id": {"$in": batch}}
        ))
        sent.extend(batch)
        return httpx.Response(200, json={"results": [], "total": len(batch), "advanced": len(batch), "skipped": 0})

    _registry(monkeypatch, handler)
    await _restore(mongo, {"registry_entries": [dict(e) for e in ENTRIES]})

    assert sorted(sent) == [e["entry_id"] for e in ENTRIES]
    assert written_when_sent and set(written_when_sent) == {0}
    assert await _entries_collection(mongo).count_documents({"namespace": NAMESPACE}) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["refused", "unreachable"])
async def test_a_registry_that_does_not_account_for_the_ids_stops_the_write(mongo, monkeypatch, answer):
    def handler(request: httpx.Request) -> httpx.Response:
        if answer == "unreachable":
            raise httpx.ConnectError("no route", request=request)
        return httpx.Response(503, text="registry unavailable")

    _registry(monkeypatch, handler)
    with pytest.raises(RestoreEngineError, match="nothing of this batch was written"):
        await _engine(mongo)._insert_batch(
            _entries_collection(mongo), [dict(e) for e in ENTRIES], "registry_entries",
        )
    assert await _entries_collection(mongo).count_documents({"namespace": NAMESPACE}) == 0


@pytest.mark.asyncio
async def test_a_merge_advances_the_entries_it_inserts(mongo, monkeypatch):
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.extend(item["entry_id"] for item in json.loads(request.content))
        return httpx.Response(200, json={"results": [], "total": 0, "advanced": 0, "skipped": 0})

    _registry(monkeypatch, handler)
    engine = _engine(mongo)
    await engine._merge_insert_rows(
        NAMESPACE, _entries_collection(mongo), "registry_entries",
        [dict(e) for e in ENTRIES[:3]], 2,
    )
    assert sent == [e["entry_id"] for e in ENTRIES[:3]]


@pytest.mark.asyncio
async def test_other_rows_ask_nothing_of_the_registry(mongo, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected Registry call {request.url}")

    _registry(monkeypatch, handler)
    db_name, coll_name = COLLECTION_MAP["terminologies"]
    await _engine(mongo)._insert_batch(
        mongo[db_name][coll_name],
        [{"terminology_id": "P1297-T1", "namespace": NAMESPACE, "value": "V", "status": "active"}],
        "terminologies",
    )
