"""Restore verification: a precondition before any write, one verdict after.

Engine-method tests with a stubbed reporting client — no HTTP, no mongo I/O
(the phase methods touch only the injected client and the progress emitter).
Covers the operator rulings: stale reporting schema refuses without the
drop_stale_reporting opt-in and drops with it; a reporting failure never halts
the restore, and the verdict reporting-sync computed is recorded as it was
given; an unreachable reporting-sync is an unverified verdict, never a failure.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from document_store.services.backup_engine import (
    DirectRestoreEngine,
    RestoreEngineError,
)


def _engine(reporting, events):
    eng = DirectRestoreEngine(
        MagicMock(), None, lambda ev: events.append(ev),
        reporting_client=reporting,
    )
    # Shrink the bounded waits — the logic under test is the decision,
    # not the pacing.
    eng._REPORTING_SYNC_STALL_S = 0.05
    eng._REPORTING_SYNC_MAX_S = 5
    eng._REPORTING_POLL_INTERVAL_S = 0.01
    return eng


def _reporting_stub(parity_result):
    stub = MagicMock()
    stub.parity = AsyncMock(return_value=parity_result)
    stub.trigger_batch_sync = AsyncMock(return_value=True)
    stub.drop_namespace_schema = AsyncMock(return_value=True)
    return stub


def _reason(name, issue, detail, kind="template"):
    """One finding as reporting-sync serves it."""
    return {"kind": kind, "name": name, "issue": issue, "detail": detail,
            "expected": None, "actual": None}


CLEAN = {"schema_present": False, "table_count": 0, "bookkeeping_tables_ok": True,
         "structural_issues": 0, "templates": [], "ok": True,
         "verdict": "verified", "reasons": []}
STALE = {"schema_present": True, "table_count": 5, "bookkeeping_tables_ok": True,
         "schema_name": "ns1", "structural_issues": 0, "templates": []}
# A table missing during the restore: reporting proved it, and says so.
BROKEN = dict(
    CLEAN, ok=False, verdict="failed", structural_issues=1, templates=[
        {"template_value": "T1", "table_present": False, "missing_columns": [],
         "error": None, "actual_rows": None, "expected_documents": None},
    ],
    reasons=[_reason("T1", "table_missing", "T1: no reporting table in the namespace's schema")],
)
# Reporting-sync still working: nothing it says about rows is settled yet.
def _still_running(rows=None, expected=None):
    return dict(
        CLEAN, ok=False, verdict="unverified", templates=[
            {"template_value": "T1", "actual_rows": rows, "expected_documents": expected},
        ],
        reasons=[_reason("ns1", "sync_running",
                        "namespace 'ns1' has a batch sync still running", kind="namespace")],
    )


class TestPrecondition:
    @pytest.mark.asyncio
    async def test_clean_schema_passes(self):
        events = []
        eng = _engine(_reporting_stub(CLEAN), events)
        await eng._check_reporting_precondition("ns1", drop_stale=False)

    @pytest.mark.asyncio
    async def test_stale_schema_refuses_without_flag(self):
        eng = _engine(_reporting_stub(STALE), [])
        with pytest.raises(RestoreEngineError, match="drop_stale_reporting"):
            await eng._check_reporting_precondition("ns1", drop_stale=False)

    @pytest.mark.asyncio
    async def test_stale_schema_drops_with_flag(self):
        reporting = _reporting_stub(STALE)
        events = []
        eng = _engine(reporting, events)
        await eng._check_reporting_precondition("ns1", drop_stale=True)
        reporting.drop_namespace_schema.assert_awaited_once_with("ns1")
        assert any(ev.phase == "phase_reporting_drop" for ev in events)

    @pytest.mark.asyncio
    async def test_failed_drop_refuses(self):
        reporting = _reporting_stub(STALE)
        reporting.drop_namespace_schema = AsyncMock(return_value=False)
        eng = _engine(reporting, [])
        with pytest.raises(RestoreEngineError, match="Could not drop"):
            await eng._check_reporting_precondition("ns1", drop_stale=True)

    @pytest.mark.asyncio
    async def test_unusable_bookkeeping_refuses(self):
        bad = dict(CLEAN, bookkeeping_tables_ok=False,
                   bookkeeping_error="predates namespace keying")
        eng = _engine(_reporting_stub(bad), [])
        with pytest.raises(RestoreEngineError, match="bookkeeping"):
            await eng._check_reporting_precondition("ns1", drop_stale=False)

    @pytest.mark.asyncio
    async def test_unreachable_reporting_warns_and_disables(self):
        reporting = _reporting_stub(None)
        events = []
        eng = _engine(reporting, events)
        await eng._check_reporting_precondition("ns1", drop_stale=False)
        assert eng._reporting is None
        assert any(ev.phase == "warning" for ev in events)


class TestNoHaltOnReporting:
    """The ruling this case makes: reporting is derived data, so a restore
    carries on and says what reporting looks like."""

    @pytest.mark.asyncio
    async def test_a_missing_table_does_not_stop_the_verify_phase(self):
        eng = _engine(_reporting_stub(BROKEN), [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert eng._reporting_verdicts["ns1"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_the_verdict_is_recorded_as_reporting_sync_gave_it(self):
        """document-store copies reporting-sync's verdict and reasons; it does
        not read the counters and form its own."""
        events = []
        eng = _engine(_reporting_stub(BROKEN), events)
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        verdict = eng._reporting_verdicts["ns1"]
        assert verdict["reasons"] == BROKEN["reasons"]
        assert verdict["reason"] == "T1: no reporting table in the namespace's schema"
        warning = [ev for ev in events if ev.phase == "warning"][-1].message
        assert "reporting failed" in warning
        assert "sync/batch?namespace=ns1" in warning
        assert "the restore's data is complete in MongoDB" in warning

    @pytest.mark.asyncio
    async def test_an_answer_without_a_verdict_is_unverified_not_guessed(self):
        """An older reporting-sync serves the counters only. Reading them here
        would be the second copy of the verdict this case removes — even when
        they happen to look clean."""
        old = {k: v for k, v in CLEAN.items() if k != "verdict"}
        eng = _engine(_reporting_stub(old), [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert eng._reporting_verdicts["ns1"]["status"] == "unverified"
        assert eng._reporting_verdicts["ns1"]["reason"] == "reporting-sync gave no verdict"


class TestVerifyPhase:
    @pytest.mark.asyncio
    async def test_verified_completes_without_a_warning(self):
        events = []
        eng = _engine(_reporting_stub(CLEAN), events)
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert not any(ev.phase == "warning" for ev in events)
        assert eng._reporting_verdicts["ns1"] == {
            "status": "verified", "reason": None, "reasons": []}

    @pytest.mark.asyncio
    async def test_a_recorded_verified_asked_for_its_rows(self):
        """``verified`` on the job reads as "the documents reached the tables",
        so the verdict phase asks the counting form — the structure-only answer
        is not what backs that claim."""
        reporting = _reporting_stub(CLEAN)
        eng = _engine(reporting, [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert [c.kwargs["include_counts"] for c in reporting.parity.await_args_list] == [True]

    @pytest.mark.asyncio
    async def test_the_structure_only_precondition_records_no_verdict(self):
        """The cheap form runs before any write, where it gates the restore on
        shape; it never becomes the verdict the job reports."""
        eng = _engine(_reporting_stub(dict(CLEAN, counts_included=False)), [])
        await eng._check_reporting_precondition("ns1", drop_stale=False)
        assert eng._reporting_verdicts == {}

    @pytest.mark.asyncio
    async def test_an_unverified_that_waiting_cannot_fix_is_recorded_at_once(self):
        """A template store that did not answer is settled the moment it is
        heard: the wait is for a sync, not for luck."""
        unreadable = dict(
            CLEAN, ok=False, verdict="unverified",
            reasons=[_reason("ns1", "templates_unreadable",
                            "the namespace's templates could not be listed", kind="namespace")],
        )
        reporting = _reporting_stub(unreadable)
        eng = _engine(reporting, [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        reporting.parity.assert_awaited_once()
        assert eng._reporting_verdicts["ns1"]["status"] == "unverified"

    @pytest.mark.asyncio
    async def test_a_sync_still_running_is_waited_for(self):
        answers = [_still_running(0, 100), _still_running(0, 100), CLEAN]
        reporting = _reporting_stub(None)
        reporting.parity = AsyncMock(side_effect=answers)
        eng = _engine(reporting, [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert reporting.parity.await_count == 3
        assert eng._reporting_verdicts["ns1"]["status"] == "verified"

    # A large namespace's catch-up takes minutes; a fixed wait declared it done
    # while it was still running. The wait follows the sync's own answer, and
    # the row count keeps a sync that writes nothing bounded.

    @staticmethod
    def _catching_up(rows_per_poll, *, settled_after=None, expected=1000):
        """Parity results that say "still syncing" while the row count
        advances by rows_per_poll per call, settling after settled_after calls
        (never, when None)."""
        calls = {"n": 0}

        async def parity(namespace, include_counts=False):
            calls["n"] += 1
            if settled_after is not None and calls["n"] > settled_after:
                return dict(CLEAN, templates=[{"template_value": "T1"}])
            return _still_running(calls["n"] * rows_per_poll, expected)

        stub = _reporting_stub(None)
        stub.parity = AsyncMock(side_effect=parity)
        return stub

    @pytest.mark.asyncio
    async def test_progress_keeps_the_wait_alive_past_the_stall_window(self):
        events = []
        eng = _engine(self._catching_up(10, settled_after=30), events)  # 30 polls ≈ 0.3 s > stall
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert not any(ev.phase == "warning" for ev in events)
        messages = [ev.message for ev in events if ev.phase == "phase_reporting_parity"]
        assert any("reporting catching up: 10 of 1000 rows" in m for m in messages)
        assert "reporting verified" in messages[-1]

    @pytest.mark.asyncio
    async def test_term_table_rows_are_progress_too(self):
        """The definitions sweep writes only the term tables while the
        document rows stand still; counting document rows alone read that as
        a stall (forward review of 864cc532, R1)."""
        calls = {"n": 0}

        async def parity(namespace, include_counts=False):
            calls["n"] += 1
            if calls["n"] > 30:  # 30 polls ≈ 0.3 s, past the 0.05 s stall
                return dict(CLEAN, templates=[{"template_value": "T1"}])
            return dict(
                _still_running(100, 100),
                derived_tables=[{"table": "terms", "actual_rows": calls["n"] * 5,
                                 "expected_rows": 500}],
            )

        reporting = _reporting_stub(None)
        reporting.parity = AsyncMock(side_effect=parity)
        events = []
        eng = _engine(reporting, events)
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert not any(ev.phase == "warning" for ev in events)
        assert eng._reporting_verdicts["ns1"]["status"] == "verified"

    @pytest.mark.asyncio
    async def test_no_progress_gives_up_after_the_stall_window(self):
        events = []
        eng = _engine(self._catching_up(0), events)
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        warning = [ev for ev in events if ev.phase == "warning"][-1].message
        assert "no reporting rows written for 0.05s" in warning
        assert "sync/batch?namespace=ns1" in warning
        assert "parity?namespace=ns1" in warning
        # The wait gave out; the verdict is still the one reporting-sync gave.
        assert eng._reporting_verdicts["ns1"]["status"] == "unverified"
        assert eng._reporting_verdicts["ns1"]["reasons"][0]["issue"] == "sync_running"

    @pytest.mark.asyncio
    async def test_endless_progress_stops_at_the_cap(self):
        eng = _engine(self._catching_up(1), [])
        eng._REPORTING_SYNC_MAX_S = 0.1
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        reason = eng._reporting_verdicts["ns1"]["reason"]
        assert "still syncing after 0.1s" in reason
        assert "still running" in reason  # and what reporting-sync said about it

    @pytest.mark.asyncio
    async def test_a_still_running_sync_that_turns_failed_is_reported_as_failed(self):
        """Rows that stop short when the sync finishes are a proven gap, and
        the restore says so — with the data still safely restored."""
        answers = [_still_running(10, 100), BROKEN]
        reporting = _reporting_stub(None)
        reporting.parity = AsyncMock(side_effect=answers)
        eng = _engine(reporting, [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert eng._reporting_verdicts["ns1"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_a_triggered_sync_is_recorded_for_the_completion(self):
        eng = _engine(_reporting_stub(CLEAN), [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert eng._reporting_synced == ["ns1"]

    @pytest.mark.asyncio
    async def test_a_refused_trigger_is_not_recorded(self):
        """The completion's own trigger is the second chance — a namespace
        whose verify-phase trigger failed must not be skipped there."""
        stub = _reporting_stub(CLEAN)
        stub.trigger_batch_sync = AsyncMock(return_value=False)
        eng = _engine(stub, [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert eng._reporting_synced == []

    @pytest.mark.asyncio
    async def test_skip_documents_skips_the_phase(self):
        reporting = _reporting_stub(CLEAN)
        eng = _engine(reporting, [])
        await eng._reporting_phase_verify("ns1", skip_documents=True)
        reporting.parity.assert_not_awaited()
        reporting.trigger_batch_sync.assert_not_awaited()
        assert eng._reporting_verdicts["ns1"]["status"] == "unverified"

    @pytest.mark.asyncio
    async def test_no_reporting_client_is_unverified(self):
        eng = DirectRestoreEngine(MagicMock(), None, lambda _e: None)
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert eng._reporting_verdicts["ns1"]["status"] == "unverified"

    @pytest.mark.asyncio
    async def test_reporting_going_away_mid_wait_is_unverified(self):
        reporting = _reporting_stub(_still_running(10, 100))
        reporting.parity = AsyncMock(side_effect=[_still_running(10, 100), None])
        eng = _engine(reporting, [])
        await eng._reporting_phase_verify("ns1", skip_documents=False)
        assert eng._reporting_verdicts["ns1"]["status"] == "unverified"
        assert "unreachable" in eng._reporting_verdicts["ns1"]["reason"]
