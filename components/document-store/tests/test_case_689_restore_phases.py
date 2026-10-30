"""Restore verification phases: precondition, structural gate, count parity.

Engine-method tests with a stubbed reporting client — no HTTP, no mongo I/O
(the phase methods touch only the injected client and the progress emitter).
Covers the operator rulings: stale reporting schema refuses without the
drop_stale_reporting opt-in and drops with it; structural failure halts; a
count mismatch completes WITH a warning; an unreachable reporting-sync
degrades to a warning and never fails the restore.
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
    eng._REPORTING_STRUCTURE_TIMEOUT_S = 0.05
    eng._REPORTING_COUNTS_STALL_S = 0.05
    eng._REPORTING_COUNTS_MAX_S = 5
    eng._REPORTING_POLL_INTERVAL_S = 0.01
    return eng


def _reporting_stub(parity_result):
    stub = MagicMock()
    stub.parity = AsyncMock(return_value=parity_result)
    stub.trigger_batch_sync = AsyncMock(return_value=True)
    stub.drop_namespace_schema = AsyncMock(return_value=True)
    return stub


CLEAN = {"schema_present": False, "table_count": 0, "bookkeeping_tables_ok": True,
         "structural_issues": 0, "templates": [], "ok": True}
STALE = {"schema_present": True, "table_count": 5, "bookkeeping_tables_ok": True,
         "schema_name": "ns1", "structural_issues": 0, "templates": []}


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


class TestStructuralGate:
    @pytest.mark.asyncio
    async def test_green_structure_proceeds(self):
        eng = _engine(_reporting_stub(CLEAN), [])
        await eng._reporting_phase_structure("ns1")

    @pytest.mark.asyncio
    async def test_persistent_structural_failure_halts(self):
        broken = dict(CLEAN, structural_issues=1, templates=[
            {"template_value": "T1", "table_present": False,
             "missing_columns": [], "error": None},
        ])
        eng = _engine(_reporting_stub(broken), [])
        with pytest.raises(RestoreEngineError, match="T1"):
            await eng._reporting_phase_structure("ns1")


class TestCountParity:
    @pytest.mark.asyncio
    async def test_parity_ok_no_warning(self):
        events = []
        eng = _engine(_reporting_stub(dict(CLEAN, ok=True)), events)
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        assert not any(ev.phase == "warning" for ev in events)

    @pytest.mark.asyncio
    async def test_mismatch_completes_with_warning(self):
        mismatch = dict(CLEAN, ok=False, templates=[
            {"template_value": "T1", "counts_match": False,
             "expected_documents": 10, "actual_rows": 3},
        ])
        events = []
        eng = _engine(_reporting_stub(mismatch), events)
        # Must NOT raise — operator ruling: complete with warning.
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        warnings = [ev for ev in events if ev.phase == "warning"]
        assert warnings and "T1" in warnings[-1].message

    @pytest.mark.asyncio
    async def test_derived_gaps_are_named_in_the_warning(self):
        """Document tables can be complete while term tables or registered
        views are not; the warning names those gaps rather than saying it
        has no detail."""
        gaps = dict(CLEAN, ok=False, derived_issues=2, derived_tables=[
            {"table": "terms", "expected_rows": 5, "actual_rows": 5, "issue": None},
            {"table": "term_relations", "expected_rows": 7, "actual_rows": 0,
             "issue": "term_relations_mismatch"},
        ], views=[{"view_name": "v_open", "present": False}])
        events = []
        eng = _engine(_reporting_stub(gaps), events)
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        warning = [ev for ev in events if ev.phase == "warning"][-1].message
        assert "term_relations term_relations_mismatch expected=7 actual=0" in warning
        assert "view v_open missing" in warning
        assert "expected=5" not in warning  # a passing table is not listed

    # CASE-967: a large namespace's catch-up takes minutes; a fixed 90 s wait
    # declared it incomplete while it was still running. The wait now follows
    # the reporting row count.

    @staticmethod
    def _catching_up(rows_per_poll, *, ok_after=None, expected=1000):
        """Parity results whose row count advances by rows_per_poll per call,
        turning ok after ok_after calls (never, when None)."""
        calls = {"n": 0}

        async def parity(namespace, include_counts=False):
            calls["n"] += 1
            if ok_after is not None and calls["n"] > ok_after:
                return dict(CLEAN, ok=True, templates=[{"template_value": "T1"}])
            rows = calls["n"] * rows_per_poll
            return dict(CLEAN, ok=False, templates=[
                {"template_value": "T1", "counts_match": False,
                 "expected_documents": expected, "actual_rows": rows},
            ])

        stub = _reporting_stub(None)
        stub.parity = AsyncMock(side_effect=parity)
        return stub

    @pytest.mark.asyncio
    async def test_progress_keeps_the_wait_alive_past_the_stall_window(self):
        events = []
        eng = _engine(self._catching_up(10, ok_after=30), events)  # 30 polls ≈ 0.3 s > 0.05 s stall
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        assert not any(ev.phase == "warning" for ev in events)
        messages = [ev.message for ev in events if ev.phase == "phase_reporting_parity"]
        assert any("reporting catching up: 10 of 1000 rows" in m for m in messages)
        assert "reporting parity verified" in messages[-1]

    @pytest.mark.asyncio
    async def test_no_progress_gives_up_after_the_stall_window(self):
        events = []
        eng = _engine(self._catching_up(0), events)
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        warning = [ev for ev in events if ev.phase == "warning"][-1].message
        assert "no reporting rows written for 0.05s" in warning
        assert "GET /api/reporting-sync/sync/batch/jobs" in warning
        assert "parity?namespace=ns1" in warning

    @pytest.mark.asyncio
    async def test_endless_progress_stops_at_the_cap(self):
        events = []
        eng = _engine(self._catching_up(1), events)
        eng._REPORTING_COUNTS_MAX_S = 0.1
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        warning = [ev for ev in events if ev.phase == "warning"][-1].message
        assert "still catching up after 0.1s" in warning

    @pytest.mark.asyncio
    async def test_a_triggered_sync_is_recorded_for_the_completion(self):
        eng = _engine(_reporting_stub(dict(CLEAN, ok=True)), [])
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        assert eng._reporting_synced == ["ns1"]

    @pytest.mark.asyncio
    async def test_a_refused_trigger_is_not_recorded(self):
        """The completion's own trigger is the second chance — a namespace
        whose count-phase trigger failed must not be skipped there."""
        stub = _reporting_stub(dict(CLEAN, ok=True))
        stub.trigger_batch_sync = AsyncMock(return_value=False)
        eng = _engine(stub, [])
        await eng._reporting_phase_counts("ns1", skip_documents=False)
        assert eng._reporting_synced == []

    @pytest.mark.asyncio
    async def test_skip_documents_skips_count_phase(self):
        reporting = _reporting_stub(CLEAN)
        eng = _engine(reporting, [])
        await eng._reporting_phase_counts("ns1", skip_documents=True)
        reporting.parity.assert_not_awaited()
