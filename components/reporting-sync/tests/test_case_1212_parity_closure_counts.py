"""Parity sees every table a namespace's documents need, folds every signal
into ok, and never says "not ok" without a gap; the closure walk is
pair-based and a failed rebuild is retried; listing counts are bounded and
never scan a view.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reporting_sync import term_closure
from reporting_sync.main import _relation_count, init_postgres_schema
from reporting_sync.parity import NamespaceParityResult, TemplateParity, check_namespace_parity
from reporting_sync.parity_monitor import _gaps
from reporting_sync.schema_manager import SchemaManager

from .conftest import requires_postgres

NS = "rs1212"
SHARED = {
    "template_id": "tpl-shared", "namespace": "wip", "value": "shared_visit", "version": 1,
    "fields": [{"name": "title", "type": "string"}], "identity_fields": ["title"],
    "reporting": {"sync_enabled": True}, "_foreign_namespace": "wip",
}


def _stubs(foreign: list, doc_total: int) -> ExitStack:
    stack = ExitStack()
    for target, value in (
        ("_fetch_namespace_templates", []),
        ("_fetch_foreign_templates", (foreign, [])),
        ("_expected_document_total", doc_total),
        ("_expected_active_terms", 0),
        ("_expected_active_relations", 0),
    ):
        stack.enter_context(patch(f"reporting_sync.parity.{target}", new=AsyncMock(return_value=value)))
    return stack


@requires_postgres
@pytest.mark.asyncio
async def test_a_shared_templates_table_in_the_namespace_is_checked(pg_pool):
    await init_postgres_schema(pg_pool)
    await SchemaManager(pg_pool).ensure_table_for_template(NS, SHARED)
    with _stubs([SHARED], doc_total=0):
        result = await check_namespace_parity(pg_pool, NS)
    row = result.templates[0]
    assert (row.template_value, row.template_namespace) == ("shared_visit", "wip")
    assert row.structural_ok and row.counts_match is True
    assert result.ok is True


@requires_postgres
@pytest.mark.asyncio
async def test_documents_on_a_shared_template_without_its_table_are_a_gap(pg_pool):
    await init_postgres_schema(pg_pool)
    await SchemaManager(pg_pool).ensure_schema(NS)
    with _stubs([SHARED], doc_total=3):
        result = await check_namespace_parity(pg_pool, NS)
    assert result.structural_issues == 1 and result.ok is False
    assert "shared_template:wip:shared_visit" in _gaps(result)[0]


@pytest.mark.asyncio
async def test_foreign_templates_come_from_the_namespaces_documents(monkeypatch):
    import httpx

    from reporting_sync import parity

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/template-facets"):
            return httpx.Response(200, json={"namespace": NS, "facets": [
                {"template_id": "tpl-own", "template_value": "own", "document_count": 4},
                {"template_id": "tpl-shared", "template_value": "shared_visit",
                 "template_namespace": "wip", "document_count": 2},
                {"template_id": "tpl-same", "template_value": "OWN", "document_count": 1},
                {"template_id": "tpl-empty", "template_value": "x", "document_count": 0},
            ]})
        assert request.url.path.endswith("/templates/tpl-shared")
        return httpx.Response(200, json={"template_id": "tpl-shared", "namespace": "wip"})

    real = httpx.AsyncClient
    monkeypatch.setattr(parity.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handle), **k))
    owned = [{"template_id": "tpl-own", "value": "own"}]
    foreign, unreadable = await parity._fetch_foreign_templates(NS, owned)
    assert [(t["template_id"], t["_foreign_namespace"]) for t in foreign] == [("tpl-shared", "wip")]
    assert unreadable == []


def _result(**kw) -> NamespaceParityResult:
    base = {"namespace": NS, "schema_name": NS, "schema_present": True, "table_count": 1,
            "bookkeeping_tables_ok": True}
    return NamespaceParityResult(**{**base, **kw})


def test_a_missing_entity_view_or_bookkeeping_row_is_a_repairable_gap():
    row = TemplateParity(template_value="v", table_present=True, version_tables=[1],
                         view_present=False, bookkeeping_row=True)
    assert row.structural_ok and not row.derived_ok
    assert _gaps(_result(templates=[row])) == ({"template:v"}, set())


def test_not_ok_is_never_gapless():
    assert _gaps(_result(bookkeeping_error="template-store unavailable"))[1] == {"parity_check_failed"}
    row = TemplateParity(template_value="v", table_present=True, version_tables=[1],
                         view_present=True, bookkeeping_row=True)
    assert "schema_missing" in _gaps(_result(schema_present=False, templates=[row]))[1]


# ---------------------------------------------------------------------------
# Closure
# ---------------------------------------------------------------------------


@requires_postgres
@pytest.mark.asyncio
async def test_a_diamond_closure_has_one_row_per_pair_at_its_shortest_depth(pg_pool):
    from reporting_sync.worker import SyncWorker  # noqa: F401  # tables via schema manager

    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    terms = await sm.ensure_terms_table(NS)
    relations = await sm.ensure_term_relations_table(NS)
    async with pg_pool.acquire() as conn:
        for t in ("a", "b", "c", "d"):
            await conn.execute(
                f'INSERT INTO {terms} (namespace, term_id, terminology_id, value, status) '
                "VALUES ($1, $2, 'tt', $2, 'active')", NS, t,
            )
        # d -> b -> a, d -> c -> a, and d -> a directly: three paths d..a.
        for child, parent in (("b", "a"), ("c", "a"), ("d", "b"), ("d", "c"), ("d", "a")):
            await conn.execute(
                f'INSERT INTO {relations} (namespace, source_term_id, target_term_id, relation_type, status) '
                "VALUES ($1, $2, $3, 'is_a', 'active')", NS, child, parent,
            )
    maintainer = term_closure.TermClosureMaintainer(pg_pool, sm)
    await maintainer.rebuild(NS)
    closure = await sm.ensure_term_closure_table(NS)
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT descendant_id, ancestor_id, depth FROM {closure} "
            "WHERE descendant_id = 'd' AND ancestor_id = 'a'"
        )
    assert [(r["descendant_id"], r["ancestor_id"], r["depth"]) for r in rows] == [("d", "a", 1)]


@pytest.mark.asyncio
async def test_a_failed_rebuild_is_retried(monkeypatch):
    monkeypatch.setattr(term_closure, "DEBOUNCE_SECONDS", 0.01)
    maintainer = term_closure.TermClosureMaintainer(MagicMock(), MagicMock())
    maintainer.rebuild = AsyncMock(side_effect=[RuntimeError("pg blip"), 7])
    maintainer.mark_dirty(NS)
    await asyncio.wait_for(maintainer._pending[NS], timeout=2)
    assert maintainer.rebuild.await_count == 2


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_entity_view_is_counted_as_its_version_tables():
    conn = MagicMock()
    counts = {"visits__v1": 2, "visits__v2": 3}
    conn.fetchval = AsyncMock(side_effect=lambda sql, timeout=None: counts[sql.split('"')[3]])
    # What each view reads, as the catalog records it: the bare view is a
    # cross_version_view selecting version 2 only; __entities reads both.
    reads = {"visits": ["visits__v2"], "visits__entities": ["visits__v1", "visits__v2"]}
    conn.fetch = AsyncMock(side_effect=lambda sql, schema, view: [{"relname": r} for r in reads.get(view, [])])
    tables = {"visits__v1", "visits__v2", "other__v1"}
    assert await _relation_count(conn, "ns", "visits", True, tables) == (3, None)
    assert await _relation_count(conn, "ns", "visits__entities", True, tables) == (5, None)
    count, why = await _relation_count(conn, "ns", "v_report", True, tables)
    assert count is None and "not counted" in why
    assert all("v_report" not in c.args[0] for c in conn.fetchval.await_args_list)


@pytest.mark.asyncio
async def test_a_count_that_takes_too_long_is_null_with_a_reason():
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=TimeoutError())
    count, why = await _relation_count(conn, "ns", "big__v1", False, set())
    assert count is None and "longer than" in why
