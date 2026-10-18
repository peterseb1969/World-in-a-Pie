"""CSV export against a real PostgreSQL.

- `POST /export/csv` takes a `namespace`, with `/query`'s semantics:
  unqualified names resolve in that namespace's schema.
- SQL is prepared before the response starts streaming, so a bad
  statement, an unknown relation or a missing table is an HTTP error.
  Before, those came back as 200 with an empty body.
"""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from reporting_sync.main import app, init_postgres_schema, state
from reporting_sync.schema_manager import SchemaManager

from .conftest import requires_postgres

pytestmark = [requires_postgres, pytest.mark.asyncio]

EXPORT = "/api/reporting-sync/export/csv"


@pytest.fixture
def http_client():
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": os.environ["WIP_AUTH_LEGACY_API_KEY"]},
    )


@pytest_asyncio.fixture
async def real_state(pg_pool):
    await init_postgres_schema(pg_pool)
    original_pool, original_read = state.postgres_pool, state.read_pool
    state.postgres_pool = state.read_pool = pg_pool
    schema = await SchemaManager(pg_pool).ensure_schema("csvns")
    async with pg_pool.acquire() as conn:
        await conn.execute(f'CREATE TABLE "{schema}"."doc_item" (document_id TEXT, title TEXT)')
        await conn.execute(f'INSERT INTO "{schema}"."doc_item" VALUES ($1, $2), ($3, $4)', "d1", "one", "d2", "two")
    yield pg_pool
    state.postgres_pool, state.read_pool = original_pool, original_read


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.strip().splitlines()]


async def test_query_export_resolves_unqualified_names_in_namespace(http_client, real_state):
    async with http_client:
        resp = await http_client.post(EXPORT, json={
            "sql": "SELECT document_id, title FROM doc_item ORDER BY document_id",
            "namespace": "csvns",
        })
    assert resp.status_code == 200, resp.text
    assert _lines(resp.text) == ["document_id,title", "d1,one", "d2,two"]


async def test_query_export_takes_params_with_namespace(http_client, real_state):
    async with http_client:
        resp = await http_client.post(EXPORT, json={
            "sql": "SELECT title FROM doc_item WHERE document_id = $1",
            "params": ["d2"],
            "namespace": "csvns",
        })
    assert resp.status_code == 200, resp.text
    assert _lines(resp.text) == ["title", "two"]


async def test_query_export_without_namespace_does_not_see_namespace_tables(http_client, real_state):
    """No namespace means search_path public: the unqualified relation is
    unknown, and that is a 400 naming it — not 200 with an empty body."""
    async with http_client:
        resp = await http_client.post(EXPORT, json={"sql": "SELECT title FROM doc_item"})
    assert resp.status_code == 400, resp.text
    assert "doc_item" in resp.json()["detail"]


async def test_query_export_schema_qualified_needs_no_namespace(http_client, real_state):
    async with http_client:
        resp = await http_client.post(EXPORT, json={
            "sql": "SELECT title FROM csvns.doc_item ORDER BY title",
        })
    assert resp.status_code == 200, resp.text
    assert _lines(resp.text) == ["title", "one", "two"]


@pytest.mark.parametrize("sql", [
    "SELECT no_such_column FROM doc_item",
    "SELEC title FROM doc_item",
])
async def test_query_export_sql_error_is_400(http_client, real_state, sql):
    async with http_client:
        resp = await http_client.post(EXPORT, json={"sql": sql, "namespace": "csvns"})
    assert resp.status_code == 400, resp.text


async def test_table_export_streams_namespace_table(http_client, real_state):
    async with http_client:
        resp = await http_client.get(EXPORT, params={"namespace": "csvns", "table": "doc_item"})
    assert resp.status_code == 200, resp.text
    assert _lines(resp.text)[0] == "document_id,title"
    assert len(_lines(resp.text)) == 3


async def test_table_export_missing_table_is_404(http_client, real_state):
    async with http_client:
        resp = await http_client.get(EXPORT, params={"namespace": "csvns", "table": "doc_absent"})
    assert resp.status_code == 404, resp.text
    assert "doc_absent" in resp.json()["detail"]
