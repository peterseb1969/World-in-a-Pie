"""Parity covers what reporting derives besides document tables.

A namespace whose terms were back after a restore but whose term_relations
and term_closure were empty used to read ok: true. Parity now checks the term
tables (with counts) and every registered view, and ok holds only when all
of them pass. The namespace batch sync's definitions companion now syncs term
relations too — its omission is how the relations went missing.
"""

import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from reporting_sync.batch_sync import BatchSyncService
from reporting_sync.parity import check_namespace_parity
from reporting_sync.schema_manager import SchemaManager
from reporting_sync.term_closure import TermClosureMaintainer

from .conftest import requires_postgres

NS = "derivedns"


def _patch_http(terms: int | Exception = 0, relations: int | Exception = 0) -> ExitStack:
    """No templates; def-store reports the given active totals (or raises)."""
    stack = ExitStack()
    for target, value in (
        ("_fetch_namespace_templates", []),
        ("_expected_document_total", 0),
        ("_expected_active_terms", terms),
        ("_expected_active_relations", relations),
    ):
        mock = AsyncMock(side_effect=value) if isinstance(value, Exception) else AsyncMock(return_value=value)
        stack.enter_context(patch(f"reporting_sync.parity.{target}", new=mock))
    return stack


async def _bookkeeping(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _wip_schema_migrations (
                namespace VARCHAR(255) NOT NULL DEFAULT 'wip',
                template_value TEXT NOT NULL,
                template_version INTEGER NOT NULL,
                migration_sql TEXT NOT NULL,
                PRIMARY KEY (namespace, template_value, template_version)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS public._wip_views (
                namespace VARCHAR(255) NOT NULL,
                view_name TEXT NOT NULL,
                view_sql TEXT NOT NULL,
                registered_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                registered_by TEXT,
                PRIMARY KEY (namespace, view_name)
            )
        """)


async def _add_terms(pool, terms: list[tuple[str, list[str], str | None]]) -> None:
    """(term_id, names, parent_term_id) rows, active, with their name rows."""
    sm = SchemaManager(pool)
    table = await sm.ensure_terms_table(NS)
    names_table = await sm.ensure_term_names_table(NS)
    async with pool.acquire() as conn:
        for term_id, names, parent in terms:
            await conn.execute(
                f"INSERT INTO {table} (term_id, namespace, terminology_id, value, aliases, "
                "parent_term_id, status) VALUES ($1, $2, 'T1', $1, $3, $4, 'active')",
                term_id, NS, json.dumps(names), parent,
            )
            for name in names:
                await conn.execute(
                    f"INSERT INTO {names_table} (namespace, term_id, terminology_id, value) "
                    "VALUES ($1, $2, 'T1', $3)",
                    NS, term_id, name,
                )


async def _add_relation(pool, source: str, target: str, relation_type: str = "part_of") -> None:
    table = await SchemaManager(pool).ensure_term_relations_table(NS)
    async with pool.acquire() as conn:
        await conn.execute(
            f"INSERT INTO {table} (namespace, source_term_id, target_term_id, relation_type) "
            "VALUES ($1, $2, $3, $4)",
            NS, source, target, relation_type,
        )


def _derived(result) -> dict:
    return {t.table: t for t in result.derived_tables}


@requires_postgres
@pytest.mark.asyncio
async def test_consistent_term_tables_verify_green(pg_pool):
    await _bookkeeping(pg_pool)
    await _add_terms(pg_pool, [("A", ["alpha"], None), ("B", [], "A")])
    await _add_relation(pg_pool, "B", "A")
    await TermClosureMaintainer(pg_pool, SchemaManager(pg_pool)).rebuild(NS)

    with _patch_http(terms=2, relations=1):
        result = await check_namespace_parity(pg_pool, NS)

    assert result.derived_issues == 0, result.derived_tables
    derived = _derived(result)
    # two active terms x two relation types in use (part_of, is_a via parent)
    assert (derived["term_closure"].expected_rows, derived["term_closure"].actual_rows) == (4, 4)
    assert result.ok is True


@requires_postgres
@pytest.mark.asyncio
async def test_relations_missing_after_restore_is_not_ok(pg_pool):
    """The reported shape: terms back, relations and closure empty — the
    document side is clean, and parity must still refuse ok."""
    await _bookkeeping(pg_pool)
    await _add_terms(pg_pool, [("A", [], None), ("B", [], None)])

    with _patch_http(terms=2, relations=1):
        result = await check_namespace_parity(pg_pool, NS)

    derived = _derived(result)
    assert derived["terms"].issue is None
    assert derived["term_relations"].issue == "term_relations_mismatch"
    assert (derived["term_relations"].expected_rows, derived["term_relations"].actual_rows) == (1, 0)
    assert result.structural_issues == 0 and result.count_mismatches == 0
    assert result.ok is False


@requires_postgres
@pytest.mark.asyncio
async def test_terms_short_is_a_mismatch(pg_pool):
    await _bookkeeping(pg_pool)
    await _add_terms(pg_pool, [("A", [], None)])

    with _patch_http(terms=3):
        result = await check_namespace_parity(pg_pool, NS)

    assert _derived(result)["terms"].issue == "terms_mismatch"
    assert result.ok is False


@requires_postgres
@pytest.mark.asyncio
async def test_closure_never_rebuilt_is_stale(pg_pool):
    await _bookkeeping(pg_pool)
    await _add_terms(pg_pool, [("A", [], None), ("B", [], None)])
    await _add_relation(pg_pool, "B", "A")

    with _patch_http(terms=2, relations=1):
        stale = await check_namespace_parity(pg_pool, NS)
    assert _derived(stale)["term_closure"].issue == "term_closure_stale"
    assert stale.ok is False

    await TermClosureMaintainer(pg_pool, SchemaManager(pg_pool)).rebuild(NS)
    with _patch_http(terms=2, relations=1):
        rebuilt = await check_namespace_parity(pg_pool, NS)
    assert _derived(rebuilt)["term_closure"].issue is None
    assert rebuilt.ok is True


@requires_postgres
@pytest.mark.asyncio
async def test_closure_older_than_a_new_term_is_stale(pg_pool):
    await _bookkeeping(pg_pool)
    await _add_terms(pg_pool, [("A", [], None), ("B", [], None)])
    await _add_relation(pg_pool, "B", "A")
    await TermClosureMaintainer(pg_pool, SchemaManager(pg_pool)).rebuild(NS)
    await _add_terms(pg_pool, [("C", [], None)])

    with _patch_http(terms=3, relations=1):
        result = await check_namespace_parity(pg_pool, NS)

    closure = _derived(result)["term_closure"]
    assert (closure.issue, closure.expected_rows, closure.actual_rows) == ("term_closure_stale", 3, 2)


@requires_postgres
@pytest.mark.asyncio
async def test_named_term_without_name_rows(pg_pool):
    await _bookkeeping(pg_pool)
    await _add_terms(pg_pool, [("A", ["alpha"], None), ("B", ["beta"], None)])
    async with pg_pool.acquire() as conn:
        await conn.execute(f'DELETE FROM "{NS}"."term_names" WHERE term_id = $1', "B")

    with _patch_http(terms=2):
        result = await check_namespace_parity(pg_pool, NS)

    names = _derived(result)["term_names"]
    assert (names.issue, names.expected_rows, names.actual_rows) == ("term_names_missing", 2, 1)
    assert result.ok is False


@requires_postgres
@pytest.mark.asyncio
async def test_missing_registered_view_is_reported_without_counts(pg_pool):
    """View presence is structure: the cheap form reports it too, and it is
    kept out of structural_issues, which the restore's structure gate reads
    before views are back."""
    await _bookkeeping(pg_pool)
    sm = SchemaManager(pg_pool)
    schema = await sm.ensure_schema(NS)
    async with pg_pool.acquire() as conn:
        await conn.execute(f'CREATE VIEW "{schema}"."kept" AS SELECT 1 AS one')
        await conn.executemany(
            "INSERT INTO public._wip_views (namespace, view_name, view_sql) VALUES ($1, $2, 'SELECT 1')",
            [(NS, "kept"), (NS, "lost")],
        )

    with _patch_http():
        result = await check_namespace_parity(pg_pool, NS, include_counts=False)

    assert {v.view_name: v.present for v in result.views} == {"kept": True, "lost": False}
    assert result.derived_tables == []
    assert result.derived_issues == 1
    assert result.structural_issues == 0
    assert result.ok is False


@requires_postgres
@pytest.mark.asyncio
async def test_def_store_unreachable_fails_the_check(pg_pool):
    await _bookkeeping(pg_pool)

    with _patch_http(terms=RuntimeError("def-store down"), relations=0):
        result = await check_namespace_parity(pg_pool, NS)

    terms = _derived(result)["terms"]
    assert terms.issue == "check_failed" and "def-store down" in (terms.error or "")
    # the other checks still ran
    assert _derived(result)["term_relations"].issue is None
    assert result.ok is False


@requires_postgres
@pytest.mark.asyncio
async def test_empty_namespace_is_green(pg_pool):
    """No term tables at all and nothing in def-store: nothing to miss."""
    await _bookkeeping(pg_pool)
    with _patch_http():
        result = await check_namespace_parity(pg_pool, NS)
    assert result.derived_issues == 0
    assert result.ok is True


@pytest.mark.asyncio
async def test_batch_sync_definitions_include_term_relations():
    """The namespace batch sync rebuilds relations after terms, so a restore
    that triggers it gets its relations and closure back."""
    service = BatchSyncService.__new__(BatchSyncService)
    calls: list[str] = []

    def recorder(name):
        async def record(**kwargs):
            assert kwargs["namespace"] == NS
            calls.append(name)
            return {"synced": 0, "failed": 0, "total": 0, "complete": True}
        return record

    service.batch_sync_terminologies = recorder("terminologies")
    service.batch_sync_terms = recorder("terms")
    service.batch_sync_term_relations = recorder("term_relations")

    await service._sync_definitions(NS, 100)

    assert calls == ["terminologies", "terms", "term_relations"]


def _client_returning(*payloads):
    from unittest.mock import MagicMock

    responses = []
    for payload in payloads:
        r = MagicMock()
        r.json.return_value = payload
        responses.append(r)
    client = MagicMock()
    client.get = AsyncMock(side_effect=responses)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
async def test_expected_terms_sum_active_terms_per_terminology():
    """Active terms are counted per terminology of the namespace, never from
    the terminology's denormalized term_count."""
    from reporting_sync import parity

    client = _client_returning(
        {"items": [{"terminology_id": "T1", "term_count": 99},
                   {"terminology_id": "T2", "term_count": 99}], "pages": 1},
        {"total": 3},
        {"total": 4},
    )
    with patch("reporting_sync.parity.httpx.AsyncClient", return_value=client):
        assert await parity._expected_active_terms(NS) == 7

    listing, *term_calls = client.get.call_args_list
    assert listing.kwargs["params"]["namespace"] == NS
    for call in term_calls:
        assert call.kwargs["params"]["status"] == "active"
        assert call.kwargs["params"]["namespace"] == NS


@pytest.mark.asyncio
async def test_expected_relations_count_active_in_namespace():
    from reporting_sync import parity

    client = _client_returning({"total": 5})
    with patch("reporting_sync.parity.httpx.AsyncClient", return_value=client):
        assert await parity._expected_active_relations(NS) == 5
    params = client.get.call_args.kwargs["params"]
    assert (params["namespace"], params["status"]) == (NS, "active")
