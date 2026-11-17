"""Every definitions sweep says so while it runs.

Parity's "still syncing" comes from the service that runs the sync, because a
sweep leaves no job record a check could find. That service only ever knew the
batch-all pre-sync and the document jobs, so the sweeps launched by the startup
backfill, by the standalone ``POST /sync/batch/<what>`` endpoints and by the
parity monitor's own repairs were invisible: after a restart ``/parity`` proved
``terms_mismatch`` against a term table the backfill was still filling, and
``failed`` is precisely the verdict that advises ``force=true`` — dropping the
tables out from under the sync writing them.

Tracking lives in the sweeps themselves now, at the one place they are defined,
so every present caller is covered and a caller written tomorrow is too. The
debounced closure rebuild lags its tables the same way and is covered with them.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reporting_sync.batch_sync import BatchSyncService
from reporting_sync.parity_monitor import ParityMonitor

NS = "sweepns"

# (public sweep, the body a test replaces to look inside the sweep)
_SWEEPS = [
    ("batch_sync_terminologies", "_sweep_terminologies"),
    ("batch_sync_templates", "_sweep_templates"),
    ("batch_sync_terms", "_sweep_terms"),
    ("batch_sync_term_relations", "_sweep_term_relations"),
]

# (endpoint, the sweep it runs)
_ENDPOINTS = [
    ("trigger_terminology_sync", "_sweep_terminologies"),
    ("trigger_template_metadata_sync", "_sweep_templates"),
    ("trigger_term_sync", "_sweep_terms"),
    ("trigger_term_relation_sync", "_sweep_term_relations"),
]


def _service() -> BatchSyncService:
    return BatchSyncService(None)  # type: ignore[arg-type] — no I/O here


def _stub_sweep(monkeypatch: pytest.MonkeyPatch, service: BatchSyncService, body: str,
                flags: list[bool]) -> None:
    """Replace one sweep's body with one that only records whether its own
    namespace reads as still syncing while it runs."""

    async def fake(**_kwargs: object) -> dict:
        flags.append(service.is_namespace_sync_running(NS))
        return {"synced": 0, "failed": 0, "total": 0, "complete": True}

    monkeypatch.setattr(service, body, fake)


class TestSweepsRecordThemselves:
    @pytest.mark.parametrize(("public", "body"), _SWEEPS)
    def test_every_sweep_marks_its_namespace_while_it_runs(
        self, monkeypatch: pytest.MonkeyPatch, public: str, body: str
    ) -> None:
        """The four sweeps share one choke point, so the marker cannot be
        forgotten by one of them — which is how this started."""
        service = _service()
        flags: list[bool] = []
        _stub_sweep(monkeypatch, service, body, flags)

        asyncio.run(getattr(service, public)(namespace=NS))

        assert flags == [True]
        assert service.is_namespace_sync_running(NS) is False

    def test_an_instance_wide_sweep_covers_every_namespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``namespace=None`` is the startup form: it writes every schema, so no
        namespace may read as settled while it runs."""
        service = _service()
        flags: list[bool] = []
        _stub_sweep(monkeypatch, service, "_sweep_terms", flags)

        asyncio.run(service.batch_sync_terms())

        assert flags == [True]
        assert service.is_namespace_sync_running(NS) is False

    def test_another_namespaces_sweep_leaves_this_one_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _service()
        flags: list[bool] = []

        async def fake(**_kwargs: object) -> dict:
            flags.append(service.is_namespace_sync_running("somewhere-else"))
            return {"synced": 0, "failed": 0, "complete": True}

        monkeypatch.setattr(service, "_sweep_terms", fake)
        asyncio.run(service.batch_sync_terms(namespace=NS))

        assert flags == [False]

    def test_two_sweeps_of_one_scope_stay_marked_until_the_last_ends(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The startup backfill runs the template-metadata sweep beside the
        terminology chain: the first of them finishing must not uncover a scope
        the other is still writing."""
        service = _service()
        marks: list[bool] = []

        async def slow(**_kwargs: object) -> dict:
            await asyncio.sleep(0.02)
            return {"synced": 0, "failed": 0, "complete": True}

        async def quick(**_kwargs: object) -> dict:
            return {"synced": 0, "failed": 0, "complete": True}

        monkeypatch.setattr(service, "_sweep_terms", slow)
        monkeypatch.setattr(service, "_sweep_term_relations", quick)

        async def scenario() -> None:
            whole = asyncio.ensure_future(service.batch_sync_terms(namespace=NS))
            await asyncio.sleep(0.005)
            await service.batch_sync_term_relations(namespace=NS)
            marks.append(service.is_namespace_sync_running(NS))
            await whole

        asyncio.run(scenario())

        assert marks == [True]
        assert service.is_namespace_sync_running(NS) is False

    def test_the_parity_route_reads_a_running_sweep_as_still_syncing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The endpoint asks the service, so a sweep started anywhere turns
        /parity into "still syncing" — the answer CASE-1328's restore waits on."""
        from reporting_sync.main import _namespace_sync_running, state

        service = _service()
        monkeypatch.setattr(state, "batch_sync_service", service, raising=False)
        flags: list[bool] = []

        async def fake(**_kwargs: object) -> dict:
            flags.append(_namespace_sync_running(NS))
            return {"synced": 0, "failed": 0, "complete": True}

        monkeypatch.setattr(service, "_sweep_terms", fake)
        asyncio.run(service.batch_sync_terms(namespace=NS))

        assert flags == [True]


class TestEachCallerIsCovered:
    """The callers are not changed by this fix; they are covered by it. Each
    one drives a real sweep wrapper, so the marker is observed on the path that
    used to leave the sync invisible."""

    @pytest.mark.asyncio
    async def test_the_startup_backfill_runs_tracked_sweeps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from reporting_sync import main

        service = _service()
        flags: list[bool] = []
        for _, body in _SWEEPS:
            _stub_sweep(monkeypatch, service, body, flags)
        with patch.object(main, "_source_healthy", AsyncMock(return_value=True)):
            await main._initial_metadata_sync(service)

        assert flags == [True] * len(_SWEEPS)
        assert service.is_namespace_sync_running(NS) is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("endpoint", "body"), _ENDPOINTS)
    async def test_a_standalone_endpoint_runs_a_tracked_sweep(
        self, monkeypatch: pytest.MonkeyPatch, endpoint: str, body: str
    ) -> None:
        from reporting_sync import main

        service = _service()
        monkeypatch.setattr(main.state, "batch_sync_service", service, raising=False)
        flags: list[bool] = []
        _stub_sweep(monkeypatch, service, body, flags)

        answer = await getattr(main, endpoint)(namespace=NS, page_size=10)

        assert answer["status"] == "completed"
        assert flags == [True]
        assert service.is_namespace_sync_running(NS) is False

    @pytest.mark.asyncio
    async def test_batch_alls_definitions_pre_sync_is_tracked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _service()
        flags: list[bool] = []
        for _, body in _SWEEPS:
            _stub_sweep(monkeypatch, service, body, flags)
        monkeypatch.setattr(service, "_list_templates", AsyncMock(return_value=[]))

        await service.start_batch_sync_all(namespace=NS)
        await asyncio.gather(*service._background_tasks)

        assert flags == [True] * len(_SWEEPS)
        assert service.is_namespace_sync_running(NS) is False

    @pytest.mark.asyncio
    async def test_a_monitor_repair_runs_a_tracked_sweep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _service()
        flags: list[bool] = []
        _stub_sweep(monkeypatch, service, "_sweep_terms", flags)
        monitor = ParityMonitor(MagicMock(), service, interval_seconds=60)

        await monitor._repair(NS, "terms", 1)

        assert flags == [True]
        assert service.is_namespace_sync_running(NS) is False


class TestTheMonitorAsksTheService:
    @pytest.mark.asyncio
    async def test_a_namespace_whose_sweep_is_running_is_left_to_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pass must not even look: a mid-backfill terms gap repaired on a
        sighting is a second sync fighting the first."""
        service = _service()
        monitor = ParityMonitor(
            MagicMock(), service, interval_seconds=60,
            list_namespaces=AsyncMock(return_value=[NS]),
        )
        parity = AsyncMock()
        monkeypatch.setattr(
            "reporting_sync.parity_monitor.check_namespace_parity", parity,
        )
        release = asyncio.Event()

        async def blocked(**_kwargs: object) -> dict:
            await release.wait()
            return {"synced": 0, "failed": 0, "complete": True}

        monkeypatch.setattr(service, "_sweep_terms", blocked)
        sweep = asyncio.ensure_future(service.batch_sync_terms(namespace=NS))
        await asyncio.sleep(0.01)

        await monitor.check_namespace(NS)

        assert parity.await_count == 0
        assert monitor.status().namespaces[NS].skipped == "batch sync active"

        release.set()
        await sweep
        await monitor.check_namespace(NS)
        assert parity.await_count == 1
        assert monitor.status().namespaces[NS].skipped is None

    def test_the_skip_rule_is_the_services(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """One rule, asked once: the monitor no longer reads the job list for
        its own narrower version of it."""
        service = _service()
        monitor = ParityMonitor(MagicMock(), service, interval_seconds=60)
        asked: list[str] = []

        def answer(namespace: str) -> bool:
            asked.append(namespace)
            return True

        monkeypatch.setattr(service, "is_namespace_sync_running", answer)

        assert monitor._sync_active(NS) is True
        assert asked == [NS]


class TestTheClosureRebuildIsCovered:
    """A queued rebuild leaves term_closure behind its terms for as long as
    the debounce waits, and nothing on the tables says so."""

    @pytest.mark.asyncio
    async def test_a_queued_rebuild_reads_as_still_syncing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _service()
        monkeypatch.setattr("reporting_sync.term_closure.DEBOUNCE_SECONDS", 0)
        rebuilt: list[str] = []

        async def fake_rebuild(namespace: str) -> int:
            rebuilt.append(namespace)
            return 0

        monkeypatch.setattr(service.closure, "rebuild", fake_rebuild)

        service.closure.mark_dirty(NS)
        assert service.is_namespace_sync_running(NS) is True
        assert service.closure.has_pending(NS) is True

        await service.closure._pending[NS]

        assert rebuilt == [NS]
        assert service.is_namespace_sync_running(NS) is False

    def test_a_namespace_with_nothing_queued_does_not(self) -> None:
        assert _service().closure.has_pending(NS) is False
