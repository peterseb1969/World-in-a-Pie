"""A restore says, in its result, what reporting looks like.

A restore never stops on reporting — but the job still has to say whether the
reporting layer came out right, in a form a script can branch on. The
completion result carries ``reporting_verification``: reporting-sync's verdict
per namespace the run wrote (verified / failed / unverified, with a one-line
reason and the findings behind it) and the worst of them as the overall status.

The verdict and its reasons are reporting-sync's, computed over the tables it
owns. This engine records what it is told; it keeps no judgement of its own, so
there is no second copy of the verdict to drift from the first.
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
from tests.test_case_689_restore_phases import (
    BROKEN,
    CLEAN,
    _engine,
    _reason,
    _reporting_stub,
)
from tests.test_case_942_restore_provenance import (
    NAMESPACE,
    TERMINOLOGY,
    _http,
    _reader,
)
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


TEMPLATE = {
    "template_id": "TPL-968", "namespace": NAMESPACE, "value": "THING", "version": 1,
    "identity_fields": ["code"],
}
DOCUMENT = {
    "document_id": "DOC-968", "namespace": NAMESPACE, "template_id": "TPL-968",
    "template_value": "THING", "template_version": 1, "version": 1, "status": "active",
    "data": {"code": "X-1"},
}

# What reporting-sync answers for a namespace whose tables are behind.
GAP_REASONS = [
    {"kind": "template", "name": "T1", "issue": "count_mismatch",
     "detail": "T1: 3 of 10 live documents have rows", "expected": 10, "actual": 3},
    {"kind": "template", "name": "T2", "issue": "table_missing",
     "detail": "T2: no reporting table in the namespace's schema",
     "expected": None, "actual": None},
    {"kind": "table", "name": "term_relations", "issue": "term_relations_mismatch",
     "detail": "term_relations: 0 rows where 7 are expected", "expected": 7, "actual": 0},
    {"kind": "view", "name": "v_open", "issue": "view_missing",
     "detail": "registered view 'v_open' is gone — re-register it",
     "expected": None, "actual": None},
]
GAPPED = dict(
    CLEAN, ok=False, verdict="failed", reasons=GAP_REASONS,
    structural_issues=1, count_mismatches=1, derived_issues=2,
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
        eng = _engine(_reporting_stub(CLEAN), [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        v = _verdict(eng, ["ns1"])
        assert v == {"status": "verified", "namespaces": {
            "ns1": {"status": "verified", "reason": None, "reasons": []}}}

    @pytest.mark.asyncio
    async def test_a_failed_verdict_is_recorded_with_reporting_syncs_reasons(self):
        """The findings are copied as served — this engine does not read the
        counters and assemble its own list."""
        eng = _engine(_reporting_stub(GAPPED), [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        ns1 = _verdict(eng, ["ns1"])["namespaces"]["ns1"]
        assert ns1["status"] == "failed"
        assert ns1["reasons"] == GAP_REASONS
        assert ns1["reason"].startswith("T1: 3 of 10")
        assert "(and 1 more)" in ns1["reason"]

    @pytest.mark.asyncio
    async def test_unreachable_reporting_is_unverified_for_this_and_later_namespaces(self):
        stub = _reporting_stub(None)
        eng = _engine(stub, [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        await eng._reporting_phase_verify("ns2", skip_documents=False)
        v = _verdict(eng, ["ns1", "ns2"])
        assert v["status"] == "unverified"
        assert "count" not in (v["namespaces"]["ns1"]["reason"] or "")
        assert "unreachable" in v["namespaces"]["ns1"]["reason"]
        assert v["namespaces"]["ns2"] == {
            "status": "unverified", "reason": "reporting-sync not reachable", "reasons": []}

    @pytest.mark.asyncio
    async def test_no_reporting_client_is_unverified(self):
        eng = DirectRestoreEngine(MagicMock(), None, lambda _e: None)
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert _verdict(eng, ["ns1"])["status"] == "unverified"

    @pytest.mark.asyncio
    async def test_skipped_documents_are_unverified(self):
        eng = _engine(_reporting_stub(CLEAN), [])
        await eng._reporting_phase_verify("ns1", skip_documents=True)
        assert _verdict(eng, ["ns1"])["namespaces"]["ns1"] == {
            "status": "unverified", "reason": "documents were skipped", "reasons": []}

    @pytest.mark.asyncio
    async def test_a_namespace_the_run_never_verified_is_unverified_not_clean(self):
        eng = _engine(_reporting_stub(CLEAN), [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert _verdict(eng, ["ns1", "ns2"], "not run")["namespaces"]["ns2"] == {
            "status": "unverified", "reason": "not run", "reasons": []}

    @pytest.mark.asyncio
    async def test_the_worst_namespace_decides_the_run(self):
        eng = _engine(_reporting_stub(CLEAN), [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        eng._reporting = _reporting_stub(BROKEN)
        await eng._reporting_phase_verify("ns2", skip_documents=False)
        eng._reporting = _reporting_stub(dict(
            CLEAN, ok=False, verdict="unverified",
            reasons=[_reason("ns3", "templates_unreadable", "template store did not answer",
                            kind="namespace")],
        ))
        await eng._reporting_phase_verify("ns3", skip_documents=False)
        v = _verdict(eng, ["ns1", "ns2", "ns3", "ns4"], "not run")
        assert v["status"] == "failed"
        assert [v["namespaces"][n]["status"] for n in ("ns1", "ns2", "ns3", "ns4")] == [
            "verified", "failed", "unverified", "unverified"]
        assert v["namespaces"]["ns4"]["reason"] == "not run"

    @pytest.mark.asyncio
    async def test_an_unverified_namespace_makes_the_run_unverified(self):
        eng = _engine(_reporting_stub(CLEAN), [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        eng._reporting = _reporting_stub(dict(
            CLEAN, ok=False, verdict="unverified",
            reasons=[_reason("ns2", "nothing_checked", "nothing to check", kind="namespace")],
        ))
        await eng._reporting_phase_verify("ns2", skip_documents=False)
        assert _verdict(eng, ["ns1", "ns2"])["status"] == "unverified"

    @pytest.mark.asyncio
    async def test_a_verified_namespace_stays_verified_when_reporting_goes_away(self):
        eng = _engine(_reporting_stub(CLEAN), [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        eng._reporting_unavailable("ns1", "a later phase")
        assert _verdict(eng, ["ns1"])["status"] == "verified"

    def test_only_namespaces_the_run_wrote_are_reported(self):
        eng = DirectRestoreEngine(MagicMock(), None, lambda _e: None)
        eng._set_reporting_verdict("probed-only", "unverified", "precondition")
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


async def _run_restore(mongo, entities, reporting, tmp_path):
    events = []
    engine = DirectRestoreEngine(mongo, None, events.append, reporting_client=reporting)
    with patch(
        "document_store.services.backup_engine.ArchiveReader",
        return_value=_reader(entities),
    ), patch("httpx.AsyncClient", return_value=_http()):
        await engine.run_restore(tmp_path / "a.zip", NAMESPACE, allow_missing_identity=True)
    return events


@pytest.mark.asyncio
async def test_a_restore_reports_its_verdict(mongo, tmp_path):
    events = await _run_restore(
        mongo, {"terminologies": [TERMINOLOGY]}, _reporting_stub(CLEAN), tmp_path,
    )
    verdict = _completion(events)
    assert verdict["status"] == "verified"
    assert verdict["namespaces"][NAMESPACE]["status"] == "verified"


@pytest.mark.asyncio
async def test_a_broken_reporting_layer_does_not_stop_the_restore(mongo, tmp_path):
    """The incident class: reporting-sync could not build the tables, the gate
    halted mid-restore, and the namespace was left with its definitions and no
    documents. The tables are derived data — the restore writes the documents
    and reports what reporting looks like."""
    events = await _run_restore(
        mongo,
        {"terminologies": [TERMINOLOGY], "templates": [TEMPLATE], "documents": [DOCUMENT]},
        _reporting_stub(BROKEN),
        tmp_path,
    )
    assert not [e for e in events if e.phase == "failed"]
    verdict = _completion(events)
    assert verdict["status"] == "failed"
    assert verdict["namespaces"][NAMESPACE]["reasons"] == BROKEN["reasons"]

    db, coll = COLLECTION_MAP["documents"]
    rows = await mongo[db][coll].find({"namespace": NAMESPACE}).to_list(10)
    assert [r["document_id"] for r in rows] == ["DOC-968"]


@pytest.mark.asyncio
async def test_a_merge_that_fails_reporting_reports_failed(mongo, tmp_path):
    await mongo[_DB_REGISTRY]["namespaces"].insert_one(
        {"prefix": NAMESPACE, "isolation_mode": "open"}
    )
    events = []
    stub = _reporting_stub(GAPPED)
    try:
        engine = DirectRestoreEngine(mongo, None, events.append, reporting_client=stub)
        engine._REPORTING_SYNC_STALL_S = 0.05
        engine._REPORTING_POLL_INTERVAL_S = 0.01
        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=_reader({"terminologies": [TERMINOLOGY]}),
        ):
            await engine.run_merge(tmp_path / "m.zip", NAMESPACE, add_missing=True)
    finally:
        await mongo[_DB_REGISTRY]["namespaces"].delete_many({"prefix": NAMESPACE})

    verdict = _completion(events)
    assert verdict["status"] == "failed"
    assert {g["name"] for g in verdict["namespaces"][NAMESPACE]["reasons"]} == {
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
    assert verdict["status"] == "unverified"
    assert "does not wait for reporting" in verdict["namespaces"][REMAP_TARGET]["reason"]


@pytest.mark.asyncio
async def test_the_verdict_lands_on_the_job_record():
    """The service stores the completion details as the job's result."""
    from wip_archive.models import ProgressEvent

    from document_store.models.backup_job import BackupJobKind
    from document_store.services import backup_service

    verdict = {"status": "failed", "namespaces": {}}
    job = MagicMock(kind=BackupJobKind.BACKUP, archive_path=None)
    job.set = AsyncMock()
    with patch.object(backup_service.BackupJob, "find_one", AsyncMock(return_value=job)):
        await backup_service._persist_event("job-1", ProgressEvent(
            phase="complete", message="Restore complete", percent=100,
            details={"reporting_verification": verdict},
        ))
    fields = job.set.call_args.args[0]
    assert fields[backup_service.BackupJob.result]["reporting_verification"] == verdict
