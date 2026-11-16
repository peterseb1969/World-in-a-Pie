"""A restore has the Registry account for every entry ID it writes.

The ID-preserving restore and a merge write the Registry entries an archive
carries straight to MongoDB, so the Registry never saw their IDs. A Registry
process reads a prefix's highest existing ID once, the first time it mints
from it, and only raises the counter after that for IDs it is given: one that
had minted from a prefix before the restore minted the restored IDs again.
Now every entry id the run writes is sent to /entries/advance-counters
before the first write (batched, 1000 per call), and a Registry that does
not answer for all of them stops the run with nothing written. (The first
version advanced per insert batch, so a Registry that failed after batch 1
left batch 1 written behind a message saying nothing was: FRanC's forward
review, CASE-1297#4.)
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


def _answer(batch: list) -> httpx.Response:
    return httpx.Response(200, json={
        "results": [], "total": len(batch), "advanced": len(batch), "skipped": 0,
    })


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
        return _answer(batch)

    _registry(monkeypatch, handler)
    await _restore(mongo, {"registry_entries": [dict(e) for e in ENTRIES]})

    assert sorted(sent) == [e["entry_id"] for e in ENTRIES]
    assert written_when_sent == [0]  # one call, every id, before any row
    assert await _entries_collection(mongo).count_documents({"namespace": NAMESPACE}) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["refused", "unreachable", "short", "second_call"])
async def test_a_registry_that_does_not_account_for_every_id_writes_nothing(
    mongo, monkeypatch, answer,
):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        batch = json.loads(request.content)
        if answer == "unreachable":
            raise httpx.ConnectError("no route", request=request)
        if answer == "refused":
            return httpx.Response(404, text="Not Found")
        if answer == "short":
            return httpx.Response(200, json={"results": [], "total": len(batch) - 1})
        if calls["n"] == 2:  # the Registry stops answering mid-way
            return httpx.Response(503, text="registry unavailable")
        return _answer(batch)

    _registry(monkeypatch, handler)
    monkeypatch.setattr(DirectRestoreEngine, "_ADVANCE_BATCH", 2)
    with pytest.raises(RestoreEngineError, match="nothing was written"):
        await _restore(mongo, {"registry_entries": [dict(e) for e in ENTRIES]})
    assert await _entries_collection(mongo).count_documents({"namespace": NAMESPACE}) == 0


def _merge_engine(order: list[tuple], *, advance_fails: bool = False) -> DirectRestoreEngine:
    engine = DirectRestoreEngine(MagicMock(), None, lambda _e: None)

    async def advance(entry_ids):
        order.append(("advance", sorted(entry_ids)))
        if advance_fails:
            raise RestoreEngineError("the Registry refused; nothing was written")

    async def upload(reader, staged, first_target, *, dry_run):
        order.append(("upload", dry_run))

    async def definitions(namespace, *args, **kwargs):
        order.append(("write", namespace))
        raise RestoreEngineError("stop here")

    engine._advance_registry_counters = advance
    engine._upload_merge_blobs = upload
    engine._apply_definitions = definitions
    engine.result = {"namespaces": {}}
    return engine


def _merge_stages():
    from document_store.services.backup_engine import _StagedMerge
    from document_store.services.merge_plan import EntityPlan

    return [
        _StagedMerge(
            src=ns, tgt=ns, definitions=MagicMock(), remapper=MagicMock(),
            built={"registry_entries": EntityPlan("registry_entries", to_insert=[
                {"entry_id": f"P1297-{ns}"},
            ])},
            doc_groups={}, streamed=None, arriving={}, wanted={},
        )
        for ns in ("a", "b")
    ]


async def _commit(engine, *, dry_run=False):
    return await engine._commit_staged_merges(
        MagicMock(), _merge_stages(), "a", MagicMock(), MagicMock(), MagicMock(),
        policy={"on_clash": "skip", "older_versions": "retire"},
        batch_size=10, skip_documents=False, skip_files=False, dry_run=dry_run,
    )


@pytest.mark.asyncio
async def test_a_merge_advances_every_target_s_entries_before_its_first_write():
    """Before the file bytes too (CASE-1349): a merge uploads its files before
    its records, so an advance after the upload left bytes in storage while
    the refusal said nothing was written."""
    order: list[tuple] = []
    with pytest.raises(RestoreEngineError, match="stop here"):
        await _commit(_merge_engine(order))
    assert order == [
        ("advance", ["P1297-a", "P1297-b"]), ("upload", False), ("write", "a"),
    ]


@pytest.mark.asyncio
async def test_a_merge_whose_advance_is_refused_uploads_no_file():
    order: list[tuple] = []
    with pytest.raises(RestoreEngineError, match="nothing was written"):
        await _commit(_merge_engine(order, advance_fails=True))
    assert order == [("advance", ["P1297-a", "P1297-b"])]


@pytest.mark.asyncio
async def test_a_merge_dry_run_asks_the_registry_nothing():
    order: list[tuple] = []
    assert await _commit(_merge_engine(order), dry_run=True) is False
    assert order == [("upload", True)]


@pytest.mark.asyncio
async def test_an_insert_batch_asks_nothing_of_the_registry(mongo, monkeypatch):
    """The advance is the run's, before any write; writing a batch of
    entries (or anything else) does not call the Registry."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected Registry call {request.url}")

    _registry(monkeypatch, handler)
    await _engine(mongo)._insert_batch(
        _entries_collection(mongo), [dict(e) for e in ENTRIES], "registry_entries",
    )


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
