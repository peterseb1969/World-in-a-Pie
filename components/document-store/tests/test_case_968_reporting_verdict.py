"""A restore says in its result whether reporting was verified.

The reporting verification used to surface only as a free-text warning, so a
script could not tell "verified" from "gave up after the wait" — the job
ended `complete` either way. The completion result now carries
``reporting_verification``: a verdict per namespace the run wrote
(verified / incomplete / unavailable / not_checked, with a reason and the
gaps) and the worst of them as the overall status.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.backup_engine import (
    _DB_REGISTRY,
    COLLECTION_MAP,
    PROVENANCE_COLLECTION,
    DirectRestoreEngine,
)
from tests.test_case_689_restore_phases import CLEAN, _engine, _reporting_stub
from tests.test_case_942_restore_provenance import NAMESPACE, TERMINOLOGY, _http, _reader
from tests.test_merge_restore import REMAP_TARGET, _clear_remap_target, _run_remap


@pytest_asyncio.fixture
async def mongo():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        db, coll = PROVENANCE_COLLECTION
        await client[db][coll].delete_many({"namespace": {"$in": [NAMESPACE, REMAP_TARGET]}})
        for db_name, coll_name in COLLECTION_MAP.values():
            await client[db_name][coll_name].delete_many({"namespace": NAMESPACE})

    await _clear()
    yield client
    await _clear()
    client.close()

GAPPED = dict(
    CLEAN,
    ok=False,
    templates=[
        {"template_value": "T1", "table_present": True, "counts_match": False,
         "expected_documents": 10, "actual_rows": 3},
        {"template_value": "T2", "table_present": False},
        {"template_value": "T3", "table_present": True, "counts_match": True,
         "expected_documents": 4, "actual_rows": 4},
    ],
    derived_tables=[
        {"table": "terms", "issue": None, "expected_rows": 5, "actual_rows": 5},
        {"table": "term_relations", "issue": "term_relations_mismatch",
         "expected_rows": 7, "actual_rows": 0},
    ],
    views=[{"view_name": "v_open", "present": False}, {"view_name": "v_ok", "present": True}],
)


def _verdict(eng, namespaces, reason="unchecked"):
    eng._finish_reporting_verification(namespaces, reason)
    return eng.result["reporting_verification"]


class TestVerdicts:
    @pytest.mark.asyncio
    async def test_parity_held_is_verified(self):
        eng = _engine(_reporting_stub(dict(CLEAN, ok=True)), [])
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        v = _verdict(eng, ["ns1"])
        assert v == {"status": "verified", "namespaces": {
            "ns1": {"status": "verified", "reason": None, "gaps": []}}}

    @pytest.mark.asyncio
    async def test_giving_up_is_incomplete_with_the_gaps(self):
        eng = _engine(_reporting_stub(GAPPED), [])
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        v = _verdict(eng, ["ns1"])
        ns1 = v["namespaces"]["ns1"]
        assert v["status"] == ns1["status"] == "incomplete"
        assert "no reporting rows written" in ns1["reason"]
        assert ns1["gaps"] == [
            {"kind": "template", "name": "T1", "issue": "count_mismatch",
             "expected": 10, "actual": 3},
            {"kind": "template", "name": "T2", "issue": "table_missing",
             "expected": None, "actual": None},
            {"kind": "table", "name": "term_relations", "issue": "term_relations_mismatch",
             "expected": 7, "actual": 0},
            {"kind": "view", "name": "v_open", "issue": "view_missing",
             "expected": None, "actual": None},
        ]

    @pytest.mark.asyncio
    async def test_unreachable_reporting_is_unavailable_for_this_and_later_namespaces(self):
        stub = _reporting_stub(None)
        eng = _engine(stub, [])
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        await eng._reporting_phase_counts("ns2", skip_documents=False)
        v = _verdict(eng, ["ns1", "ns2"])
        assert v["status"] == "unavailable"
        assert "count parity" in v["namespaces"]["ns1"]["reason"]
        assert v["namespaces"]["ns2"] == {
            "status": "unavailable", "reason": "reporting-sync not reachable", "gaps": []}

    @pytest.mark.asyncio
    async def test_no_reporting_client_is_unavailable(self):
        eng = DirectRestoreEngine(MagicMock(), None, lambda _e: None)
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        assert _verdict(eng, ["ns1"])["status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_skipped_documents_are_not_checked(self):
        eng = _engine(_reporting_stub(dict(CLEAN, ok=True)), [])
        await eng._reporting_phase_counts("ns1", skip_documents=True)
        assert _verdict(eng, ["ns1"])["namespaces"]["ns1"] == {
            "status": "not_checked", "reason": "documents were skipped", "gaps": []}

    @pytest.mark.asyncio
    async def test_the_worst_namespace_decides_the_run(self):
        eng = _engine(_reporting_stub(dict(CLEAN, ok=True)), [])
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        eng._reporting = _reporting_stub(GAPPED)
        await eng._reporting_phase_counts("ns2", skip_documents=False)
        v = _verdict(eng, ["ns1", "ns2", "ns3"], "not run")
        assert v["status"] == "incomplete"
        assert [v["namespaces"][n]["status"] for n in ("ns1", "ns2", "ns3")] == [
            "verified", "incomplete", "not_checked"]
        assert v["namespaces"]["ns3"]["reason"] == "not run"

    @pytest.mark.asyncio
    async def test_a_verified_namespace_stays_verified_when_reporting_goes_away(self):
        eng = _engine(_reporting_stub(dict(CLEAN, ok=True)), [])
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        eng._reporting_unavailable("ns1", "a later phase")
        assert _verdict(eng, ["ns1"])["status"] == "verified"

    def test_only_namespaces_the_run_wrote_are_reported(self):
        eng = DirectRestoreEngine(MagicMock(), None, lambda _e: None)
        eng._set_reporting_verdict("probed-only", "unavailable", "precondition")
        assert list(_verdict(eng, ["written"])["namespaces"]) == ["written"]


# ---------------------------------------------------------------------------
# End to end: each restore mode puts the verdict on its completion result
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def _beanie(session_mongo_client):
    # The remap path re-qualifies synonyms through Beanie models.
    from tests.conftest import _ensure_beanie
    await _ensure_beanie(session_mongo_client)


def _completion(events):
    [complete] = [e for e in events if e.phase == "complete"]
    return complete.details["reporting_verification"]


@pytest.mark.asyncio
async def test_a_restore_reports_its_verdict(mongo, tmp_path):
    events = []
    engine = DirectRestoreEngine(
        mongo, None, events.append, reporting_client=_reporting_stub(dict(CLEAN, ok=True)),
    )
    with patch(
        "document_store.services.backup_engine.ArchiveReader",
        return_value=_reader({"terminologies": [TERMINOLOGY]}),
    ), patch("httpx.AsyncClient", return_value=_http()):
        await engine.run_restore(tmp_path / "a.zip", NAMESPACE, allow_missing_identity=True)

    verdict = _completion(events)
    assert verdict["status"] == "verified"
    assert verdict["namespaces"][NAMESPACE]["status"] == "verified"


@pytest.mark.asyncio
async def test_a_merge_that_gives_up_reports_incomplete(mongo, tmp_path):
    await mongo[_DB_REGISTRY]["namespaces"].insert_one(
        {"prefix": NAMESPACE, "isolation_mode": "open"}
    )
    events = []
    stub = _reporting_stub(GAPPED)
    try:
        engine = DirectRestoreEngine(mongo, None, events.append, reporting_client=stub)
        engine._REPORTING_COUNTS_STALL_S = 0.05
        engine._REPORTING_POLL_INTERVAL_S = 0.01
        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=_reader({"terminologies": [TERMINOLOGY]}),
        ):
            await engine.run_merge(tmp_path / "m.zip", NAMESPACE, add_missing=True)
    finally:
        await mongo[_DB_REGISTRY]["namespaces"].delete_many({"prefix": NAMESPACE})

    verdict = _completion(events)
    assert verdict["status"] == "incomplete"
    assert {g["name"] for g in verdict["namespaces"][NAMESPACE]["gaps"]} == {
        "T1", "T2", "term_relations", "v_open"}


@pytest.mark.asyncio
async def test_a_fresh_restore_says_it_did_not_check(mongo):
    await _clear_remap_target(mongo)
    await mongo[_DB_REGISTRY]["namespaces"].insert_one(
        {"prefix": REMAP_TARGET, "description": "", "isolation_mode": "open"}
    )
    events = []
    try:
        await _run_remap(mongo, _reader({"terminologies": [TERMINOLOGY]}), events)
    finally:
        await _clear_remap_target(mongo)

    verdict = _completion(events)
    assert verdict["status"] == "not_checked"
    assert "does not wait for reporting" in verdict["namespaces"][REMAP_TARGET]["reason"]


@pytest.mark.asyncio
async def test_the_verdict_lands_on_the_job_record():
    """The service stores the completion details as the job's result."""
    from wip_archive.models import ProgressEvent

    from document_store.models.backup_job import BackupJobKind
    from document_store.services import backup_service

    verdict = {"status": "incomplete", "namespaces": {}}
    job = MagicMock(kind=BackupJobKind.BACKUP, archive_path=None)
    job.set = AsyncMock()
    with patch.object(backup_service.BackupJob, "find_one", AsyncMock(return_value=job)):
        await backup_service._persist_event("job-1", ProgressEvent(
            phase="complete", message="Restore complete", percent=100,
            details={"reporting_verification": verdict},
        ))
    fields = job.set.call_args.args[0]
    assert fields[backup_service.BackupJob.result]["reporting_verification"] == verdict
