"""A backup that leaves out documents leaves out their Registry entries too.

Restored without their documents, those entries claim the documents'
identity keys for entry ids with nothing behind them: a later create with the
same identity is deduped onto an entry that resolves to nothing. The restore
side already drops them when its own skip flags say so; an archive written
with skip_documents carried them anyway, so a normal restore of it brought
them in.
"""

from __future__ import annotations

import uuid

import pytest
from wip_archive.archive import ArchiveReader

from document_store.services.backup_engine import COLLECTION_MAP, DirectBackupEngine


def _entry(ns: str, entity_type: str) -> dict:
    return {"entry_id": uuid.uuid4().hex, "namespace": ns, "entity_type": entity_type,
            "primary_composite_key": {}, "synonyms": []}


@pytest.fixture
async def seeded(session_mongo_client):
    ns = f"skipdoc-{uuid.uuid4().hex[:8]}"
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
    db_r, coll_r = COLLECTION_MAP["registry_entries"]
    await session_mongo_client[db_r][coll_r].insert_many(
        [_entry(ns, "templates"), _entry(ns, "documents")],
    )
    yield ns
    for db_name, coll_name in COLLECTION_MAP.values():
        await session_mongo_client[db_name][coll_name].delete_many({"namespace": ns})


async def _entry_types(mongo, ns: str, path, **kw) -> list[str]:
    await DirectBackupEngine(mongo, None, lambda _e: None).run_backup([ns], path, **kw)
    with ArchiveReader(path) as reader:
        return sorted(r["entity_type"] for r in reader.read_entities("registry_entries", namespace=ns))


@pytest.mark.asyncio
async def test_a_skip_documents_backup_carries_no_document_entries(session_mongo_client, seeded, tmp_path):
    types = await _entry_types(session_mongo_client, seeded, tmp_path / "a.zip", skip_documents=True)
    assert types == ["templates"]


@pytest.mark.asyncio
async def test_a_full_backup_still_carries_them(session_mongo_client, seeded, tmp_path):
    types = await _entry_types(session_mongo_client, seeded, tmp_path / "a.zip")
    assert types == ["documents", "templates"]


def test_a_dry_run_counts_neither_dropped_edges_nor_their_entries():
    """drop_unresolved_edges leaves an edge's document rows and its Registry
    entry out of the real run; the dry run's counts leave them out too."""
    from unittest.mock import MagicMock

    from document_store.services.backup_engine import _would_restore

    rows = {
        "documents": [{"document_id": "E1", "version": 1}, {"document_id": "E1", "version": 2},
                      {"document_id": "D1", "version": 1}],
        "registry_entries": [{"entry_id": "E1", "entity_type": "documents"},
                             {"entry_id": "D1", "entity_type": "documents"},
                             {"entry_id": "T1", "entity_type": "templates"}],
    }
    reader = MagicMock()
    reader.read_entities = MagicMock(side_effect=lambda et, namespace="": iter(rows.get(et, [])))
    entry = MagicMock()
    entry.counts.documents, entry.counts.registry_entries = 3, 3

    assert _would_restore(reader, entry, "ns", "documents", set(), {"E1"}) == 1
    assert _would_restore(reader, entry, "ns", "registry_entries", set(), {"E1"}) == 2
    assert _would_restore(reader, entry, "ns", "documents", set(), set()) == 3
