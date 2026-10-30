"""Namespace-scoped keys read and own their registered views.

Phase 2b C4 made raw SQL (/query) and view writes admin/service-only, which
left a namespace-scoped key no way to read a registered view — and, once
views became app-owned bootstrap artifacts, no way to register its own.
Two additions close both halves:

- POST /namespace/{ns}/views/{name}/rows — structured read (no caller SQL:
  closed operator set, columns validated against the view, bound params),
  gated on namespace READ, served from the read-only reporting role.
- Registration and deletion accept a namespace-ADMIN key, with containment:
  the created view may only depend on relations in the namespace's own
  schema (checked via pg_depend on the view Postgres actually built, inside
  the registration transaction — an uncontained view rolls back). The
  privileged groups keep registering uncontained views deliberately.
"""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from reporting_sync.main import app, init_postgres_schema, state

from .conftest import requires_postgres

ADMIN = os.environ["WIP_AUTH_LEGACY_API_KEY"]
NSADMIN = "rs_nsadmin_key"  # admin on ns-a
READER = "rs_reader_key"  # read on ns-a
NSADMIN_XREAD = "rs_nsadmin_xread_key"  # admin on ns-a, read on ns-other
XREADER = "rs_xreader_key"  # read on ns-a and ns-other

NS = "ns-a"
P = f"/api/reporting-sync/namespace/{NS}"


def _client(key: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": key},
    )


@pytest_asyncio.fixture
async def real_state(pg_pool, scoped_reads):
    """Point the app's global state at the real test pool; namespace-scoped
    readers read on their own scoped connections."""
    await init_postgres_schema(pg_pool)
    original_pool, original_read = state.postgres_pool, state.read_pool
    # Role separation is not under test here: one pool serves both.
    state.postgres_pool = state.read_pool = pg_pool
    yield pg_pool
    state.postgres_pool, state.read_pool = original_pool, original_read


@pytest_asyncio.fixture
async def seeded(real_state):
    """ns-a schema with a probe table, plus a foreign schema to reach for."""
    from reporting_sync.schema_manager import SchemaManager

    sm = SchemaManager(real_state)
    schema = await sm.ensure_schema(NS)
    async with real_state.acquire() as conn:
        await conn.execute(
            f'CREATE TABLE "{schema}"."doc_probe__v1" '
            "(document_id TEXT PRIMARY KEY, title TEXT, score INT, status TEXT)"
        )
        await conn.executemany(
            f'INSERT INTO "{schema}"."doc_probe__v1" VALUES ($1, $2, $3, $4)',
            [
                ("d1", "alpha", 10, "active"),
                ("d2", "beta", 20, "active"),
                ("d3", "gamma", 30, "retired"),
            ],
        )
        await conn.execute('CREATE SCHEMA IF NOT EXISTS "ns-other"')
        await conn.execute(
            'CREATE TABLE "ns-other"."doc_secret__v1" (document_id TEXT)'
        )
    return schema


async def _register(key: str, sql: str, name: str = "v_probe"):
    async with _client(key) as c:
        return await c.post(f"{P}/views", json={"name": name, "sql": sql})


async def _rows(key: str, body: dict | None = None, name: str = "v_probe"):
    async with _client(key) as c:
        return await c.post(f"{P}/views/{name}/rows", json=body)


@requires_postgres
@pytest.mark.asyncio
async def test_nsadmin_registers_and_reader_reads(seeded):
    r = await _register(NSADMIN, "SELECT document_id, title, score, status FROM doc_probe__v1")
    assert r.status_code == 201, r.text

    # Bare read: whole view, columns from the view's own description.
    r = await _rows(READER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["columns"] == ["document_id", "title", "score", "status"]
    assert body["row_count"] == 3
    assert body["truncated"] is False
    assert {"document_id": "d1", "title": "alpha", "score": 10, "status": "active"} in body["rows"]

    # Structured filters, bound values, sort, paging.
    r = await _rows(READER, {
        "filters": [{"column": "status", "op": "eq", "value": "active"}],
        "sort": [{"column": "score", "direction": "desc"}],
    })
    assert r.status_code == 200, r.text
    assert [row["document_id"] for row in r.json()["rows"]] == ["d2", "d1"]

    r = await _rows(READER, {
        "filters": [{"column": "document_id", "op": "in", "value": ["d1", "d3"]}],
        "sort": [{"column": "document_id", "direction": "asc"}],
    })
    assert [row["document_id"] for row in r.json()["rows"]] == ["d1", "d3"]

    r = await _rows(READER, {
        "sort": [{"column": "score", "direction": "asc"}], "limit": 2,
    })
    body = r.json()
    assert [row["document_id"] for row in body["rows"]] == ["d1", "d2"]
    assert body["truncated"] is True
    r = await _rows(READER, {
        "sort": [{"column": "score", "direction": "asc"}], "limit": 2, "offset": 2,
    })
    body = r.json()
    assert [row["document_id"] for row in body["rows"]] == ["d3"]
    assert body["truncated"] is False


@requires_postgres
@pytest.mark.asyncio
async def test_empty_result_still_reports_columns(seeded):
    await _register(NSADMIN, "SELECT document_id, title FROM doc_probe__v1")
    r = await _rows(READER, {
        "filters": [{"column": "title", "op": "eq", "value": "nope"}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"] == []
    assert body["columns"] == ["document_id", "title"]


@requires_postgres
@pytest.mark.asyncio
async def test_unknown_filter_column_is_a_400_naming_the_columns(seeded):
    await _register(NSADMIN, "SELECT document_id FROM doc_probe__v1")
    r = await _rows(READER, {"filters": [{"column": "nope", "value": 1}]})
    assert r.status_code == 400
    assert "document_id" in r.text


@requires_postgres
@pytest.mark.asyncio
async def test_only_registered_views_are_readable(seeded):
    # Not registered at all.
    r = await _rows(READER, name="v_missing")
    assert r.status_code == 404
    # A real table is not a registered view — this is the views surface.
    r = await _rows(READER, name="doc_probe__v1")
    assert r.status_code == 404


@requires_postgres
@pytest.mark.asyncio
async def test_nsadmin_view_must_stay_inside_its_schema(seeded, real_state):
    # Another namespace's schema: refused, and the registration rolls back.
    r = await _register(
        NSADMIN, 'SELECT document_id FROM "ns-other".doc_secret__v1', name="v_leak",
    )
    assert r.status_code == 403, r.text
    assert "ns-other" in r.text
    assert (await _rows(NSADMIN, name="v_leak")).status_code == 404
    async with real_state.acquire() as conn:
        assert not await conn.fetchval(
            "SELECT TRUE FROM public._wip_views WHERE namespace = $1 AND view_name = 'v_leak'",
            NS,
        )

    # Public bookkeeping is outside too.
    r = await _register(NSADMIN, "SELECT view_name FROM public._wip_views", name="v_leak")
    assert r.status_code == 403

    # The privileged groups keep registering cross-schema views.
    r = await _register(
        ADMIN, 'SELECT document_id FROM "ns-other".doc_secret__v1', name="v_leak",
    )
    assert r.status_code == 201, r.text
    # Reading it takes read on every namespace it draws from: a reader of
    # ns-a alone gets the view's 404, a reader of both gets its rows.
    assert (await _rows(READER, name="v_leak")).status_code == 404
    assert (await _rows(XREADER, name="v_leak")).status_code == 200


@requires_postgres
@pytest.mark.asyncio
async def test_nsadmin_may_draw_on_namespaces_it_can_read(seeded):
    r = await _register(
        NSADMIN_XREAD, 'SELECT document_id FROM "ns-other".doc_secret__v1', name="v_x",
    )
    assert r.status_code == 201, r.text
    assert (await _rows(XREADER, name="v_x")).status_code == 200
    assert (await _rows(READER, name="v_x")).status_code == 404

    # Platform tables stay out of reach of a namespace key.
    r = await _register(NSADMIN_XREAD, "SELECT view_name FROM public._wip_views", name="v_p")
    assert r.status_code == 403


@requires_postgres
@pytest.mark.asyncio
async def test_a_view_over_platform_tables_is_read_by_privileged_keys_only(seeded):
    r = await _register(ADMIN, "SELECT view_name FROM public._wip_views", name="v_p")
    assert r.status_code == 201, r.text
    assert (await _rows(READER, name="v_p")).status_code == 404
    assert (await _rows(ADMIN, name="v_p")).status_code == 200


@requires_postgres
@pytest.mark.asyncio
async def test_contained_view_may_stack_on_own_schema_views(seeded):
    await _register(NSADMIN, "SELECT document_id, score FROM doc_probe__v1")
    r = await _register(NSADMIN, "SELECT * FROM v_probe WHERE score > 15", name="v_top")
    assert r.status_code == 201, r.text
    r = await _rows(READER, name="v_top")
    assert {row["document_id"] for row in r.json()["rows"]} == {"d2", "d3"}


@requires_postgres
@pytest.mark.asyncio
async def test_nsadmin_replaces_and_deletes_and_reads_survive_replace(seeded):
    await _register(NSADMIN, "SELECT document_id, title FROM doc_probe__v1")
    for _ in range(4):
        assert (await _rows(READER)).status_code == 200

    # Replace (DROP + CREATE) with a different column set, read immediately.
    r = await _register(NSADMIN, "SELECT document_id FROM doc_probe__v1")
    assert r.status_code == 200, r.text
    for i in range(8):
        r = await _rows(READER)
        assert r.status_code == 200, f"read {i} after replace: {r.text}"
        assert r.json()["columns"] == ["document_id"]

    async with _client(NSADMIN) as c:
        assert (await c.delete(f"{P}/views/v_probe")).status_code == 200
    assert (await _rows(READER)).status_code == 404
