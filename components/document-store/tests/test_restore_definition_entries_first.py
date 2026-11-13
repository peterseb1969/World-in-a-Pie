"""A restore writes the definitions' Registry entries before the reporting gate.

Every service resolves a template or terminology id through the Registry.
The plain restore used to write ALL registry entries last, after the
documents, so while the reporting structure gate ran no restored template
resolved: reporting-sync's batch sync listed each template's documents by id,
document-store answered 404, reporting-sync (rightly) read that as "source did
not answer" and created no table, and the gate timed out — "table missing" for
every template, the restore halted with the namespace partial. A merge wrote
its entries after its documents the same way.

The definitions' entries now go in with the definitions; the documents' and
files' entries stay at the end. And when the gate does halt, its message
carries reporting-sync's own reason per template, not only "table missing".
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
    RestoreEngineError,
)
from tests.test_case_689_restore_phases import CLEAN, _engine, _reporting_stub
from tests.test_case_942_restore_provenance import NAMESPACE, TERMINOLOGY, _http, _reader

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
async def test_a_plain_restore_registers_definitions_before_the_reporting_gate(mongo, tmp_path):
    seen: list[set[str]] = []
    engine = DirectRestoreEngine(
        mongo, None, lambda _e: None, reporting_client=_recording_stub(mongo, seen),
    )
    with patch(
        "document_store.services.backup_engine.ArchiveReader", return_value=_reader(ENTITIES),
    ), patch("httpx.AsyncClient", return_value=_http()):
        await engine.run_restore(tmp_path / "a.zip", NAMESPACE)

    # The first trigger is the structure gate's, between templates and documents.
    assert seen[0] == {"T942", "TPL-DEFS-1"}
    db_name, coll_name = COLLECTION_MAP["registry_entries"]
    restored = await mongo[db_name][coll_name].count_documents({"namespace": NAMESPACE})
    assert restored == 3  # every entry once, none twice


@pytest.mark.asyncio
async def test_a_merge_registers_definitions_before_its_documents(mongo, tmp_path):
    await mongo[_DB_REGISTRY]["namespaces"].insert_one({"prefix": NAMESPACE, "isolation_mode": "open"})
    seen: list[set[str]] = []
    engine = DirectRestoreEngine(
        mongo, None, lambda _e: None, reporting_client=_recording_stub(mongo, seen),
    )
    with patch(
        "document_store.services.backup_engine.ArchiveReader", return_value=_reader(ENTITIES),
    ):
        await engine.run_merge(tmp_path / "m.zip", NAMESPACE, add_missing=True)

    assert seen[0] == {"T942", "TPL-DEFS-1"}
    db_name, coll_name = COLLECTION_MAP["registry_entries"]
    restored = await mongo[db_name][coll_name].count_documents({"namespace": NAMESPACE})
    assert restored == 3


@pytest.mark.asyncio
async def test_a_halted_gate_says_why_reporting_sync_failed():
    broken = dict(CLEAN, structural_issues=2, templates=[
        {"template_value": "FIRESIDE", "table_present": False, "missing_columns": [], "error": None},
        {"template_value": "LESSON", "table_present": False, "missing_columns": [], "error": None},
    ])
    stub = _reporting_stub(broken)
    stub.failed_jobs = AsyncMock(return_value={
        "FIRESIDE": "Document Store answered HTTP 404 for template 019f-x",
    })
    eng = _engine(stub, [])
    with pytest.raises(RestoreEngineError) as err:
        await eng._reporting_phase_structure("ns1")
    message = str(err.value)
    assert (
        "FIRESIDE: table missing — its reporting sync failed: Document Store "
        "answered HTTP 404 for template 019f-x"
    ) in message
    assert "LESSON: table missing" in message
    assert "LESSON: table missing —" not in message  # no job error known for it
    stub.failed_jobs.assert_awaited_once_with("ns1")


@pytest.mark.asyncio
async def test_the_client_reads_back_why_its_jobs_failed(monkeypatch):
    """failed_jobs: a job that did not start gives the trigger's message, one
    that started and failed its record's error_message; one still running or
    done gives nothing."""
    import httpx

    from document_store.services import reporting_client

    started = [
        {"job_id": "j1", "template_value": "FIRESIDE", "status": "pending", "message": ""},
        {"job_id": "j2", "template_value": "LESSON", "status": "pending", "message": ""},
        {"job_id": None, "template_value": "SESSION", "status": "failed", "message": "already active"},
    ]
    records = {
        "j1": {"job_id": "j1", "template_value": "FIRESIDE", "status": "failed",
               "error_message": "Document Store answered HTTP 404 for template x"},
        "j2": {"job_id": "j2", "template_value": "LESSON", "status": "completed",
               "error_message": None},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=started)
        return httpx.Response(200, json=records[request.url.path.rsplit("/", 1)[-1]])

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        reporting_client.httpx, "AsyncClient",
        lambda *a, **kw: real(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )
    client = reporting_client.ReportingSyncClient()
    assert await client.trigger_batch_sync("kb") is True
    assert await client.failed_jobs("kb") == {
        "FIRESIDE": "Document Store answered HTTP 404 for template x",
        "SESSION": "already active",
    }
    assert await client.failed_jobs("other") == {}
