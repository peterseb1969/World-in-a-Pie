"""A backup's write detection covers its last read and never costs it the
archive; its manifest says when it carries no document versions.

The recount that detects writes during a backup ran before the referents
read, so a write during that read went unseen, and it ran inside the
backup's failure handling, so a failed recount discarded a finished
archive. A skip_documents backup stamped include_all_versions true for an
archive with no documents in it.
"""

from __future__ import annotations

import uuid

import pytest
from wip_archive.archive import ArchiveReader

from document_store.services.backup_engine import COLLECTION_MAP, DirectBackupEngine


def _warnings(events) -> list[str]:
    return [e.message for e in events if e.phase == "warning"]


@pytest.fixture
async def seeded(session_mongo_client):
    ns = f"c1294-{uuid.uuid4().hex[:8]}"
    db_t, coll_t = COLLECTION_MAP["templates"]
    await session_mongo_client[db_t][coll_t].insert_one({
        "template_id": "T", "namespace": ns, "value": "T", "version": 1,
        "status": "active", "fields": [{"name": "title", "type": "string"}],
    })
    db_d, coll_d = COLLECTION_MAP["documents"]
    await session_mongo_client[db_d][coll_d].insert_one({
        "document_id": "D", "namespace": ns, "template_id": "T", "template_version": 1,
        "version": 1, "status": "active", "data": {"title": "x"},
    })
    yield ns
    for db_name, coll_name in COLLECTION_MAP.values():
        await session_mongo_client[db_name][coll_name].delete_many({"namespace": ns})


@pytest.mark.asyncio
async def test_a_write_during_the_referents_read_is_detected(
    session_mongo_client, seeded, tmp_path, monkeypatch,
):
    events: list = []
    engine = DirectBackupEngine(session_mongo_client, None, events.append)
    original = engine._read_referents

    async def read_referents_while_a_term_is_created(entry_ids):
        db_name, coll_name = COLLECTION_MAP["terms"]
        await session_mongo_client[db_name][coll_name].insert_one(
            {"term_id": "late", "namespace": seeded, "value": "late"},
        )
        return await original(entry_ids)

    monkeypatch.setattr(engine, "_read_referents", read_referents_while_a_term_is_created)
    await engine.run_backup([seeded], tmp_path / "a.zip")

    assert any(f"{seeded} terms: 0 before, 0 read, 1 after" in w for w in _warnings(events))


@pytest.mark.asyncio
async def test_a_failed_recount_keeps_the_finished_archive(
    session_mongo_client, seeded, tmp_path, monkeypatch,
):
    events: list = []
    engine = DirectBackupEngine(session_mongo_client, None, events.append)
    original = engine._pre_count
    calls = {"n": 0}

    async def pre_count_failing_after_the_read(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:  # the first call is the count before reading
            raise ConnectionError("mongo went away")
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, "_pre_count", pre_count_failing_after_the_read)
    path = tmp_path / "a.zip"
    await engine.run_backup([seeded], path)

    assert events[-1].phase == "complete"
    assert any("Could not count" in w and "mongo went away" in w for w in _warnings(events))
    with ArchiveReader(path) as reader:
        assert [d["document_id"] for d in reader.read_entities("documents", namespace=seeded)] == ["D"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("skip_documents", "expected"), [(True, False), (False, True)])
async def test_include_all_versions_describes_the_archive(
    session_mongo_client, seeded, tmp_path, skip_documents, expected,
):
    path = tmp_path / "a.zip"
    await DirectBackupEngine(session_mongo_client, None, lambda _e: None).run_backup(
        [seeded], path, skip_documents=skip_documents,
    )
    with ArchiveReader(path) as reader:
        assert reader.read_manifest().include_all_versions is expected
