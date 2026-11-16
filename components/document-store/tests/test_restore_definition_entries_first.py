"""A restore writes the definitions' Registry entries before it syncs reporting.

Every service resolves a template or terminology id through the Registry. The
plain restore used to write ALL registry entries last, after the documents, so
when the restore triggered its batch sync no restored template resolved:
reporting-sync's sync listed each template's documents by id, document-store
answered 404, reporting-sync (rightly) read that as "source did not answer" and
created no table — "table missing" for every template. A merge wrote its
entries after its documents the same way.

The definitions' entries go in with the definitions; the documents' and files'
entries stay at the end. The restore triggers its one batch sync after the
namespace is written, so by then every id that sync resolves exists — the
tables it builds hold the restored rows, not empty ones an operator has to
resync.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.backup_engine import (
    _DB_REGISTRY,
    COLLECTION_MAP,
    PROVENANCE_COLLECTION,
    DirectRestoreEngine,
)
from tests.test_case_689_restore_phases import CLEAN, _reporting_stub
from tests.test_case_942_restore_provenance import NAMESPACE, TERMINOLOGY, _http, _reader


@pytest.fixture(autouse=True)
def _registry_counters(monkeypatch):
    """These restores run without a Registry: the counter advance a restore
    asks of it before writing entries is recorded, not sent. Its own tests are
    in test_case_1297_restored_ids_advance_counters.py."""
    advanced: list[str] = []

    async def record(self, entry_ids):
        advanced.extend(entry_ids)

    monkeypatch.setattr(DirectRestoreEngine, "_advance_registry_counters", record)
    return advanced


TEMPLATE = {
    "template_id": "TPL-DEFS-1", "namespace": NAMESPACE, "value": "THING",
    "label": "Thing", "version": 1, "status": "active", "fields": [],
}
DOCUMENT = {
    "document_id": "DOC-DEFS-1", "namespace": NAMESPACE, "template_id": "TPL-DEFS-1",
    "template_version": 1, "version": 1, "status": "active", "data": {},
}


def _entry(entry_id: str, entity_type: str) -> dict:
    return {
        "entry_id": entry_id, "namespace": NAMESPACE, "entity_type": entity_type,
        "primary_composite_key": {"value": entry_id},
        "primary_composite_key_hash": f"hash-{entry_id}",
        "synonyms": [], "status": "active",
    }


ENTITIES = {
    "terminologies": [TERMINOLOGY],
    "templates": [TEMPLATE],
    "documents": [DOCUMENT],
    "registry_entries": [
        _entry("T942", "terminologies"),
        _entry("TPL-DEFS-1", "templates"),
        _entry("DOC-DEFS-1", "documents"),
    ],
}


@pytest_asyncio.fixture
async def mongo():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        db, coll = PROVENANCE_COLLECTION
        await client[db][coll].delete_many({"namespace": NAMESPACE})
        await client[_DB_REGISTRY]["composite_key_claims"].delete_many({"namespace": NAMESPACE})
        await client[_DB_REGISTRY]["namespaces"].delete_many({"prefix": NAMESPACE})
        for db_name, coll_name in COLLECTION_MAP.values():
            await client[db_name][coll_name].delete_many({"namespace": NAMESPACE})

    await _clear()
    yield client
    await _clear()
    client.close()


def _recording_stub(mongo, seen: list[set[str]]):
    """A reporting client that records, at each batch-sync trigger, which
    registry entries of the namespace exist."""
    stub = _reporting_stub(dict(CLEAN, ok=True))
    db_name, coll_name = COLLECTION_MAP["registry_entries"]

    async def trigger(namespace):
        rows = await mongo[db_name][coll_name].find(
            {"namespace": namespace}, {"entry_id": 1}
        ).to_list(None)
        seen.append({r["entry_id"] for r in rows})
        return True

    stub.trigger_batch_sync = AsyncMock(side_effect=trigger)
    return stub


@pytest.mark.asyncio
async def test_a_plain_restore_registers_definitions_before_it_syncs_reporting(mongo, tmp_path):
    seen: list[set[str]] = []
    engine = DirectRestoreEngine(
        mongo, None, lambda _e: None, reporting_client=_recording_stub(mongo, seen),
    )
    with patch(
        "document_store.services.backup_engine.ArchiveReader", return_value=_reader(ENTITIES),
    ), patch("httpx.AsyncClient", return_value=_http()):
        await engine.run_restore(tmp_path / "a.zip", NAMESPACE)

    # The restore triggers one sync, after the documents are written: by then
    # the definitions' entries resolve.
    assert seen == [{"T942", "TPL-DEFS-1", "DOC-DEFS-1"}]
    db_name, coll_name = COLLECTION_MAP["registry_entries"]
    restored = await mongo[db_name][coll_name].count_documents({"namespace": NAMESPACE})
    assert restored == 3  # every entry once, none twice


@pytest.mark.asyncio
async def test_a_merge_syncs_reporting_once_its_entries_resolve(mongo, tmp_path):
    await mongo[_DB_REGISTRY]["namespaces"].insert_one({"prefix": NAMESPACE, "isolation_mode": "open"})
    seen: list[set[str]] = []
    engine = DirectRestoreEngine(
        mongo, None, lambda _e: None, reporting_client=_recording_stub(mongo, seen),
    )
    with patch(
        "document_store.services.backup_engine.ArchiveReader", return_value=_reader(ENTITIES),
    ):
        await engine.run_merge(tmp_path / "m.zip", NAMESPACE, add_missing=True)

    assert seen == [{"T942", "TPL-DEFS-1", "DOC-DEFS-1"}]
    db_name, coll_name = COLLECTION_MAP["registry_entries"]
    restored = await mongo[db_name][coll_name].count_documents({"namespace": NAMESPACE})
    assert restored == 3
