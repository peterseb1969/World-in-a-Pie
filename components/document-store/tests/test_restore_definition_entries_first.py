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
        "TPL-F": "Document Store answered HTTP 404 for template 019f-x",
    })
    eng = _engine(stub, [])
    eng._template_ids_of = AsyncMock(return_value={
        ("ns1", "FIRESIDE"): "TPL-F", ("ns1", "LESSON"): "TPL-L",
    })
    with pytest.raises(RestoreEngineError) as err:
        await eng._reporting_phase_structure("ns1")
    message = str(err.value)
    assert (
        "FIRESIDE: table missing — its reporting sync failed: Document Store "
        "answered HTTP 404 for template 019f-x"
    ) in message
    assert "LESSON: table missing" in message
    assert "LESSON: table missing —" not in message  # no job error known for it
    assert "Delete the namespace" in message
    stub.failed_jobs.assert_awaited_once_with("ns1", {"TPL-F", "TPL-L"})


@pytest.mark.asyncio
async def test_a_halted_merge_gate_does_not_say_to_delete_the_namespace():
    broken = dict(CLEAN, structural_issues=1, templates=[
        {"template_value": "FIRESIDE", "table_present": False, "missing_columns": [], "error": None},
    ])
    eng = _engine(_reporting_stub(broken), [])
    with pytest.raises(RestoreEngineError) as err:
        await eng._reporting_phase_structure("ns1", merge=True)
    message = str(err.value)
    assert "halting before merging documents" in message
    assert "Delete the namespace" not in message
    assert "PARTIAL" not in message
    assert "Do not delete the namespace" in message


@pytest.mark.asyncio
async def test_failing_templates_are_identified_in_their_owning_namespace(mongo):
    db_name, coll_name = COLLECTION_MAP["templates"]
    other = f"{NAMESPACE}-other"
    await mongo[db_name][coll_name].insert_many([
        {"template_id": "TPL-OWN", "namespace": NAMESPACE, "value": "SESSION", "version": 1},
        {"template_id": "TPL-OTHER", "namespace": other, "value": "SESSION", "version": 1},
        {"template_id": "TPL-SHARED", "namespace": other, "value": "SHARED", "version": 1},
    ])
    try:
        eng = DirectRestoreEngine(mongo, None, lambda ev: None, reporting_client=None)
        ids = await eng._template_ids_of(NAMESPACE, [
            {"template_value": "SESSION"},
            {"template_value": "SHARED", "template_namespace": other},
        ])
    finally:
        await mongo[db_name][coll_name].delete_many({"namespace": {"$in": [NAMESPACE, other]}})
    assert ids == {(NAMESPACE, "SESSION"): "TPL-OWN", (other, "SHARED"): "TPL-SHARED"}


def _client_with(monkeypatch, handler):
    import httpx

    from document_store.services import reporting_client

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        reporting_client.httpx, "AsyncClient",
        lambda *a, **kw: real(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )
    return reporting_client.ReportingSyncClient()


@pytest.mark.asyncio
async def test_the_client_reads_back_why_its_jobs_failed(monkeypatch):
    """failed_jobs: a job that did not start gives the trigger's message, one
    that started and failed its record's error_message; one still running or
    done gives nothing. Only the jobs of the templates asked about are read,
    by template id: a same-valued template of another namespace lends none."""
    import httpx

    started = [
        {"job_id": "j1", "template_value": "FIRESIDE", "template_id": "TPL-F",
         "status": "pending", "message": ""},
        {"job_id": "j2", "template_value": "LESSON", "template_id": "TPL-L",
         "status": "pending", "message": ""},
        {"job_id": None, "template_value": "SESSION", "template_id": "TPL-S",
         "status": "failed", "message": "already active"},
        {"job_id": "j9", "template_value": "FIRESIDE", "template_id": "TPL-F-OTHER",
         "status": "pending", "message": ""},
    ]
    records = {
        "j1": {"job_id": "j1", "status": "failed",
               "error_message": "Document Store answered HTTP 404 for template x"},
        "j2": {"job_id": "j2", "status": "completed", "error_message": None},
        "j9": {"job_id": "j9", "status": "failed", "error_message": "another namespace's"},
    }
    read: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=started)
        job_id = request.url.path.rsplit("/", 1)[-1]
        read.append(job_id)
        return httpx.Response(200, json=records[job_id])

    client = _client_with(monkeypatch, handler)
    assert await client.trigger_batch_sync("kb") is True
    assert await client.failed_jobs("kb", {"TPL-F", "TPL-L", "TPL-S"}) == {
        "TPL-F": "Document Store answered HTTP 404 for template x",
        "TPL-S": "already active",
    }
    assert sorted(read) == ["j1", "j2"]
    assert await client.failed_jobs("other", {"TPL-F"}) == {}


@pytest.mark.asyncio
async def test_the_read_back_is_bounded(monkeypatch):
    import asyncio

    import httpx

    started = [
        {"job_id": "fast", "template_value": "A", "template_id": "TPL-A", "status": "pending"},
        {"job_id": "slow", "template_value": "B", "template_id": "TPL-B", "status": "pending"},
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=started)
        if request.url.path.endswith("/slow"):
            await asyncio.sleep(5)
        return httpx.Response(200, json={"status": "failed", "error_message": "boom"})

    client = _client_with(monkeypatch, handler)
    client._FAILED_JOBS_TIMEOUT_S = 0.2
    assert await client.trigger_batch_sync("kb") is True
    loop = asyncio.get_event_loop()
    begun = loop.time()
    assert await client.failed_jobs("kb", {"TPL-A", "TPL-B"}) == {"TPL-A": "boom"}
    assert loop.time() - begun < 2
