"""The standing parity check repairs what it finds, within bounds.

A gap is repaired only on its second sighting, with the narrowest sync that
closes it, at most N times; an active batch job makes the loop step aside;
gaps with no safe repair are reported, never touched.
"""

import logging
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reporting_sync.batch_sync import BatchSyncService
from reporting_sync.models import BatchSyncStatus
from reporting_sync.parity import (
    DerivedTableParity,
    NamespaceParityResult,
    TemplateParity,
    ViewParity,
)
from reporting_sync.parity_monitor import ParityMonitor
from reporting_sync.schema_manager import SchemaManager

from .conftest import requires_postgres

NS = "monns"


def _result(*, templates=(), derived=(), views=(), bookkeeping_ok=True) -> NamespaceParityResult:
    r = NamespaceParityResult(
        namespace=NS, schema_name=NS, schema_present=True, table_count=1,
        bookkeeping_tables_ok=bookkeeping_ok,
        templates=list(templates), derived_tables=list(derived), views=list(views),
    )
    r.ok = not (templates or derived or views) and bookkeeping_ok
    return r


def _derived(table, issue):
    return DerivedTableParity(table=table, expected_rows=1, actual_rows=0, issue=issue)


def _batch_sync(active_jobs=()):
    bs = MagicMock()
    bs.list_jobs.return_value = list(active_jobs)
    bs.batch_sync_terms = AsyncMock(return_value={})
    bs.batch_sync_term_relations = AsyncMock(return_value={})
    bs.start_batch_sync = AsyncMock()
    bs.closure.rebuild = AsyncMock(return_value=0)
    bs.schema_manager.recreate_registered_views = AsyncMock(return_value=(1, 0))
    return bs


def _monitor(bs, results, *, attempts=3, namespaces=(NS,)):
    """A monitor whose parity check returns ``results`` in turn."""
    m = ParityMonitor(
        MagicMock(), bs, interval_seconds=60, repair_attempts=attempts,
        list_namespaces=AsyncMock(return_value=list(namespaces)),
    )
    patcher = patch(
        "reporting_sync.parity_monitor.check_namespace_parity",
        new=AsyncMock(side_effect=list(results)),
    )
    return m, patcher


@pytest.mark.asyncio
async def test_a_gap_is_repaired_on_its_second_sighting_only():
    gap = _result(derived=[_derived("term_relations", "term_relations_mismatch")])
    bs = _batch_sync()
    m, p = _monitor(bs, [gap, gap, _result()])
    with p:
        await m.run_once()
        bs.batch_sync_term_relations.assert_not_awaited()
        await m.run_once()
        bs.batch_sync_term_relations.assert_awaited_once_with(namespace=NS)
        assert m.status().namespaces[NS].repair_attempts == {"term_relations": 1}
        await m.run_once()  # closed
    ns = m.status().namespaces[NS]
    assert (ns.ok, ns.gaps, ns.repair_attempts, ns.not_healing) == (True, [], {}, [])
    action = m.status().recent_actions[-1]
    assert (action.gap, action.action, action.attempt) == ("term_relations", "term-relation batch sync", 1)


@pytest.mark.asyncio
async def test_a_gap_that_comes_and_goes_is_never_repaired():
    gap = _result(derived=[_derived("term_closure", "term_closure_stale")])
    bs = _batch_sync()
    m, p = _monitor(bs, [gap, _result(), gap, _result()])
    with p:
        for _ in range(4):
            await m.run_once()
    bs.closure.rebuild.assert_not_awaited()


@pytest.mark.asyncio
async def test_each_gap_gets_its_narrowest_repair_in_order():
    gap = _result(
        templates=[TemplateParity(template_value="T1", table_present=True,
                                  counts_match=False, expected_documents=3, actual_rows=1)],
        derived=[_derived("terms", "terms_mismatch"),
                 _derived("term_relations", "term_relations_mismatch"),
                 _derived("term_closure", "term_closure_stale")],
        views=[ViewParity(view_name="v", present=False)],
    )
    bs = _batch_sync()
    calls = []
    bs.batch_sync_terms.side_effect = lambda **kw: calls.append("terms")
    bs.batch_sync_term_relations.side_effect = lambda **kw: calls.append("term_relations")
    bs.schema_manager.recreate_registered_views.side_effect = (
        lambda ns: calls.append("views") or (1, 0)
    )
    bs.start_batch_sync.side_effect = lambda v, **kw: calls.append(f"template:{v}")
    m, p = _monitor(bs, [gap, gap])
    with p:
        await m.run_once()
        await m.run_once()
    # the relation sync ends with a closure rebuild, so no separate one
    assert calls == ["terms", "term_relations", "views", "template:T1"]
    bs.closure.rebuild.assert_not_awaited()
    bs.start_batch_sync.assert_awaited_once_with("T1", namespace=NS)


@pytest.mark.asyncio
async def test_term_names_gap_is_repaired_by_the_term_sync():
    gap = _result(derived=[_derived("term_names", "term_names_missing")])
    bs = _batch_sync()
    m, p = _monitor(bs, [gap, gap])
    with p:
        await m.run_once()
        await m.run_once()
    bs.batch_sync_terms.assert_awaited_once_with(namespace=NS)


@pytest.mark.asyncio
async def test_closure_alone_is_rebuilt_directly():
    gap = _result(derived=[_derived("term_closure", "term_closure_stale")])
    bs = _batch_sync()
    m, p = _monitor(bs, [gap, gap])
    with p:
        await m.run_once()
        await m.run_once()
    bs.closure.rebuild.assert_awaited_once_with(NS)


@pytest.mark.asyncio
async def test_repairs_stop_at_the_limit_and_are_reported_once(caplog):
    gap = _result(derived=[_derived("terms", "terms_mismatch")])
    bs = _batch_sync()
    m, p = _monitor(bs, [gap] * 6, attempts=2)
    with p, caplog.at_level(logging.ERROR, logger="reporting_sync.parity_monitor"):
        for _ in range(6):
            await m.run_once()
    assert bs.batch_sync_terms.await_count == 2
    not_healed = [r for r in caplog.records if "not healed after 2 repair(s): terms" in r.message]
    assert len(not_healed) == 1
    assert m.status().namespaces[NS].not_healing == ["terms"]


@pytest.mark.asyncio
async def test_a_gap_with_no_safe_repair_is_reported_never_repaired(caplog):
    gap = _result(
        templates=[TemplateParity(template_value="OLD", table_present=True, legacy_table=True)],
        derived=[DerivedTableParity(table="terms", issue="check_failed", error="def-store down")],
        bookkeeping_ok=False,
    )
    bs = _batch_sync()
    m, p = _monitor(bs, [gap, gap, gap])
    with p, caplog.at_level(logging.ERROR, logger="reporting_sync.parity_monitor"):
        for _ in range(3):
            await m.run_once()
    bs.start_batch_sync.assert_not_awaited()
    bs.batch_sync_terms.assert_not_awaited()
    expected = {"bookkeeping", "legacy_table:OLD", "check_failed:terms"}
    assert set(m.status().namespaces[NS].not_healing) == expected
    assert len([r for r in caplog.records if "needs an operator" in r.message]) == 3


@pytest.mark.asyncio
async def test_an_active_batch_job_makes_the_check_step_aside():
    gap = _result(derived=[_derived("terms", "terms_mismatch")])
    running = SimpleNamespace(status=BatchSyncStatus.RUNNING, namespace=NS)
    bs = _batch_sync(active_jobs=[running])
    m, p = _monitor(bs, [gap, gap, gap])
    with p:
        await m.run_once()
        await m.run_once()
        assert m.status().namespaces[NS].skipped == "batch sync active"
        bs.batch_sync_terms.assert_not_awaited()
        # job done: the two-sighting count starts afresh
        bs.list_jobs.return_value = []
        await m.run_once()
    bs.batch_sync_terms.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_whole_instance_job_also_blocks():
    whole = SimpleNamespace(status=BatchSyncStatus.PENDING, namespace=None)
    other = SimpleNamespace(status=BatchSyncStatus.RUNNING, namespace="elsewhere")
    done = SimpleNamespace(status=BatchSyncStatus.COMPLETED, namespace=NS)
    m = ParityMonitor(MagicMock(), _batch_sync([whole]), interval_seconds=60)
    assert m._sync_active(NS) is True
    m = ParityMonitor(MagicMock(), _batch_sync([other, done]), interval_seconds=60)
    assert m._sync_active(NS) is False


@pytest.mark.asyncio
async def test_one_failing_namespace_does_not_stop_the_pass():
    bs = _batch_sync()
    m, p = _monitor(bs, [RuntimeError("boom"), _result()], namespaces=("bad", "good"))
    with p:
        await m.run_once()
    st = m.status()
    assert st.namespaces["bad"].error == "boom"
    assert st.namespaces["good"].ok is True
    assert st.last_pass_finished_at is not None


@pytest.mark.asyncio
async def test_registry_unavailable_skips_the_pass():
    m = ParityMonitor(
        MagicMock(), _batch_sync(), interval_seconds=60,
        list_namespaces=AsyncMock(side_effect=RuntimeError("registry down")),
    )
    await m.run_once()
    assert "registry down" in m.status().last_pass_error


@pytest.mark.asyncio
async def test_a_namespace_gone_from_the_registry_leaves_the_status():
    bs = _batch_sync()
    m, p = _monitor(bs, [_result(), _result()])
    with p:
        await m.run_once()
        m._list_namespaces = AsyncMock(return_value=["other"])
        await m.run_once()
    assert list(m.status().namespaces) == ["other"]


@pytest.mark.asyncio
async def test_interval_zero_disables_the_loop():
    m = ParityMonitor(MagicMock(), _batch_sync(), interval_seconds=0)
    await m.run_forever()  # returns at once instead of looping
    assert m.status().enabled is False


@pytest.mark.asyncio
async def test_a_failed_repair_is_recorded_and_counts():
    gap = _result(views=[ViewParity(view_name="v", present=False)])
    bs = _batch_sync()
    bs.schema_manager.recreate_registered_views.return_value = (0, 1)
    m, p = _monitor(bs, [gap, gap])
    with p:
        await m.run_once()
        await m.run_once()
    action = m.status().recent_actions[-1]
    assert action.gap == "views" and "could not be recreated" in action.error
    assert m.status().namespaces[NS].repair_attempts == {"views": 1}


# ---------------------------------------------------------------------------
# Postgres: a real gap closed by a real repair
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_a_stale_closure_is_rebuilt_by_the_monitor(pg_pool):
    async with pg_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE _wip_schema_migrations (
                namespace VARCHAR(255) NOT NULL DEFAULT 'wip',
                template_value TEXT NOT NULL,
                template_version INTEGER NOT NULL,
                migration_sql TEXT NOT NULL,
                PRIMARY KEY (namespace, template_value, template_version)
            )
        """)
    sm = SchemaManager(pg_pool)
    terms = await sm.ensure_terms_table(NS)
    relations = await sm.ensure_term_relations_table(NS)
    async with pg_pool.acquire() as conn:
        for t in ("A", "B"):
            await conn.execute(
                f"INSERT INTO {terms} (term_id, namespace, terminology_id, value, status) "
                "VALUES ($1, $2, 'T1', $1, 'active')", t, NS,
            )
        await conn.execute(
            f"INSERT INTO {relations} (namespace, source_term_id, target_term_id, relation_type) "
            "VALUES ($1, 'B', 'A', 'part_of')", NS,
        )

    bs = BatchSyncService(pg_pool)
    m = ParityMonitor(
        pg_pool, bs, interval_seconds=60,
        list_namespaces=AsyncMock(return_value=[NS]),
    )
    with ExitStack() as stack:
        for target, value in (
            ("_fetch_namespace_templates", []),
            ("_expected_active_terms", 2),
            ("_expected_active_relations", 1),
        ):
            stack.enter_context(
                patch(f"reporting_sync.parity.{target}", new=AsyncMock(return_value=value))
            )
        await m.run_once()
        assert m.status().namespaces[NS].gaps == ["term_closure"]
        await m.run_once()  # second sighting: rebuilt
        await m.run_once()
    ns = m.status().namespaces[NS]
    assert (ns.ok, ns.gaps, ns.repair_attempts) == (True, [], {})
    assert m.status().recent_actions[-1].action == "closure rebuild"
