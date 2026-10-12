"""POST /query after a view replace, and the response's column contract.

Two defects in one handler, fixed together:

- Replacing a registered view (DROP + CREATE) invalidated the pooled
  connection's cached statement plan; the next read 400'd with "cached
  statement plan is invalid" because asyncpg cannot transparently
  re-prepare inside the explicit SET LOCAL search_path transaction. The
  handler now prepares explicitly per request (uncached) with a one-shot
  retry for the residual race, so the first read after a replace succeeds.
- `columns` came from row 1 of the result, so an empty result reported
  `columns: []` — "no rows" and "no columns" were indistinguishable. The
  prepared statement's own result description now supplies the names.

Rows are objects keyed by column name — the wire shape @wip/client's
ReportQueryResult declares (its `rows: unknown[][]` was corrected to match
this contract, not the other way round: array rows would have broken every
working consumer).
"""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from reporting_sync.main import app, init_postgres_schema, state

from .conftest import requires_postgres


@pytest_asyncio.fixture
def http_client():
    # /query and the views routes are privileged (Phase 2b C4) — the client
    # carries the test admin key, same idiom as the registered-views tests.
    transport = ASGITransport(app=app)
    return AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": os.environ["WIP_AUTH_LEGACY_API_KEY"]},
    )


@pytest_asyncio.fixture
async def real_state(pg_pool):
    """Point the app's global state at the real test pool."""
    await init_postgres_schema(pg_pool)
    original_pool = state.postgres_pool
    state.postgres_pool = pg_pool
    yield pg_pool
    state.postgres_pool = original_pool


NS = "clintrial"


async def _register_view(http_client, sql: str, name: str = "v_probe"):
    r = await http_client.post(
        f"/api/reporting-sync/namespace/{NS}/views",
        json={"name": name, "sql": sql},
    )
    assert r.status_code in (200, 201), r.text
    return r


async def _query(http_client, sql: str):
    return await http_client.post(
        "/api/reporting-sync/query", json={"sql": sql, "namespace": NS},
    )


async def _seed(pool):
    from reporting_sync.schema_manager import SchemaManager

    sm = SchemaManager(pool)
    schema = await sm.ensure_schema(NS)
    async with pool.acquire() as conn:
        await conn.execute(
            f'CREATE TABLE "{schema}"."doc_probe__v1" '
            "(document_id TEXT PRIMARY KEY, title TEXT, status TEXT)"
        )
        await conn.execute(
            f'INSERT INTO "{schema}"."doc_probe__v1" VALUES ($1, $2, $3)',
            "d1", "hello", "active",
        )
    return schema


@requires_postgres
@pytest.mark.asyncio
async def test_first_read_after_view_replace_succeeds(http_client, real_state):
    """The filer's deterministic repro: replace with a different column set,
    then read immediately — every read must be 200."""
    await _seed(real_state)
    await _register_view(http_client, "SELECT document_id, title FROM doc_probe__v1")

    # Warm any per-connection state across the whole pool.
    for _ in range(8):
        r = await _query(http_client, "SELECT * FROM v_probe")
        assert r.status_code == 200, r.text

    # Replace with a narrower column set (DROP + CREATE server-side) and
    # read immediately, repeatedly — no read may 400.
    await _register_view(http_client, "SELECT document_id FROM doc_probe__v1")
    for i in range(8):
        r = await _query(http_client, "SELECT * FROM v_probe")
        assert r.status_code == 200, f"read {i} after replace: {r.text}"
        assert r.json()["columns"] == ["document_id"]

    # And back to the wider set.
    await _register_view(http_client, "SELECT document_id, title FROM doc_probe__v1")
    for i in range(8):
        r = await _query(http_client, "SELECT * FROM v_probe")
        assert r.status_code == 200, f"read {i} after second replace: {r.text}"
        assert r.json()["columns"] == ["document_id", "title"]


@requires_postgres
@pytest.mark.asyncio
async def test_empty_result_still_reports_columns(http_client, real_state):
    await _seed(real_state)
    r = await _query(
        http_client,
        "SELECT document_id, title FROM doc_probe__v1 WHERE status = 'nope'",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows"] == []
    assert body["row_count"] == 0
    assert body["columns"] == ["document_id", "title"]


@requires_postgres
@pytest.mark.asyncio
async def test_rows_are_objects_keyed_by_column(http_client, real_state):
    """The wire contract ReportQueryResult (@wip/client) is typed against."""
    await _seed(real_state)
    r = await _query(http_client, "SELECT document_id, title FROM doc_probe__v1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["columns"] == ["document_id", "title"]
    assert body["rows"] == [{"document_id": "d1", "title": "hello"}]
