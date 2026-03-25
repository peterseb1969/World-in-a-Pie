"""Tests for the reporting query endpoints (cross-template joins)."""

import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from reporting_sync.main import app, state


# =========================================================================
# Fixtures
# =========================================================================


def _mock_pool():
    """Create a mock asyncpg pool with context manager support."""
    pool = MagicMock()
    conn = AsyncMock()

    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm

    return pool, conn


@pytest.fixture
def mock_state():
    """Patch the global state with a mock postgres pool."""
    pool, conn = _mock_pool()
    original_pool = state.postgres_pool
    state.postgres_pool = pool
    yield pool, conn
    state.postgres_pool = original_pool


@pytest.fixture
def http_client():
    """Create a test HTTP client."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# =========================================================================
# GET /tables
# =========================================================================


@pytest.mark.asyncio
async def test_list_tables_summary(http_client: AsyncClient, mock_state):
    """List tables without table_name returns summary (name, row_count, column_count)."""
    pool, conn = mock_state

    # Mock information_schema.tables query
    conn.fetch = AsyncMock(side_effect=[
        # First call: list all tables
        [
            {"table_name": "doc_patient"},
            {"table_name": "doc_bank_transaction"},
            {"table_name": "terminologies"},
            {"table_name": "terms"},
            {"table_name": "term_relationships"},
            {"table_name": "_wip_schema_migrations"},  # should be filtered out
        ],
        # Subsequent calls: columns for each allowed table (5 tables)
        [{"column_name": "id", "data_type": "text", "is_nullable": "NO"}],
        [{"column_name": "id", "data_type": "text", "is_nullable": "NO"},
         {"column_name": "name", "data_type": "text", "is_nullable": "YES"}],
        [{"column_name": "id", "data_type": "text", "is_nullable": "NO"}],
        [{"column_name": "id", "data_type": "text", "is_nullable": "NO"}],
        [{"column_name": "id", "data_type": "text", "is_nullable": "NO"}],
    ])
    conn.fetchval = AsyncMock(return_value=42)

    async with http_client:
        response = await http_client.get("/api/reporting-sync/tables")

    assert response.status_code == 200
    data = response.json()
    assert "tables" in data
    table_names = [t["name"] for t in data["tables"]]
    assert "doc_patient" in table_names
    assert "terminologies" in table_names
    assert "_wip_schema_migrations" not in table_names
    # Summary mode: column_count, no columns list
    patient = next(t for t in data["tables"] if t["name"] == "doc_patient")
    assert "column_count" in patient
    assert "columns" not in patient
    assert patient["row_count"] == 42
    # doc_bank_transaction has 2 columns in mock
    bank = next(t for t in data["tables"] if t["name"] == "doc_bank_transaction")
    assert bank["column_count"] == 2


@pytest.mark.asyncio
async def test_list_tables_detail(http_client: AsyncClient, mock_state):
    """List tables with table_name returns full column detail for that table."""
    pool, conn = mock_state

    conn.fetch = AsyncMock(side_effect=[
        # First call: list all tables
        [
            {"table_name": "doc_patient"},
            {"table_name": "terminologies"},
        ],
        # Column detail for doc_patient
        [
            {"column_name": "document_id", "data_type": "text", "is_nullable": "NO"},
            {"column_name": "name", "data_type": "text", "is_nullable": "YES"},
            {"column_name": "age", "data_type": "integer", "is_nullable": "YES"},
        ],
    ])
    conn.fetchval = AsyncMock(return_value=100)

    async with http_client:
        response = await http_client.get("/api/reporting-sync/tables?table_name=doc_patient")

    assert response.status_code == 200
    data = response.json()
    assert len(data["tables"]) == 1
    table = data["tables"][0]
    assert table["name"] == "doc_patient"
    assert table["row_count"] == 100
    assert "columns" in table
    assert len(table["columns"]) == 3
    assert table["columns"][0]["name"] == "document_id"
    assert table["columns"][1]["type"] == "text"


@pytest.mark.asyncio
async def test_list_tables_detail_not_found(http_client: AsyncClient, mock_state):
    """List tables with unknown table_name returns 404."""
    pool, conn = mock_state

    conn.fetch = AsyncMock(side_effect=[
        [{"table_name": "doc_patient"}],
    ])

    async with http_client:
        response = await http_client.get("/api/reporting-sync/tables?table_name=doc_nonexistent")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_tables_no_postgres(http_client: AsyncClient):
    """List tables returns 503 when PostgreSQL not connected."""
    original = state.postgres_pool
    state.postgres_pool = None
    try:
        async with http_client:
            response = await http_client.get("/api/reporting-sync/tables")
        assert response.status_code == 503
    finally:
        state.postgres_pool = original


# =========================================================================
# POST /query
# =========================================================================


@pytest.mark.asyncio
async def test_query_simple_select(http_client: AsyncClient, mock_state):
    """Simple SELECT query returns results."""
    pool, conn = mock_state

    # Mock the query results
    mock_row = MagicMock()
    mock_row.keys.return_value = ["name", "country"]
    mock_row.__getitem__ = lambda self, k: {"name": "Alice", "country": "CH"}[k]
    # Make dict() work on the row
    mock_row_dict = {"name": "Alice", "country": "CH"}

    conn.fetch = AsyncMock(return_value=[mock_row_dict])
    conn.execute = AsyncMock()

    async with http_client:
        response = await http_client.post(
            "/api/reporting-sync/query",
            json={"sql": "SELECT name, country FROM doc_patient", "params": []},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["row_count"] == 1
    assert data["truncated"] is False


@pytest.mark.asyncio
async def test_query_rejects_insert(http_client: AsyncClient, mock_state):
    """INSERT statement is rejected."""
    async with http_client:
        response = await http_client.post(
            "/api/reporting-sync/query",
            json={"sql": "INSERT INTO doc_patient VALUES ('x')"},
        )
    assert response.status_code == 400
    assert "read-only" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_query_rejects_drop(http_client: AsyncClient, mock_state):
    """DROP TABLE is rejected."""
    async with http_client:
        response = await http_client.post(
            "/api/reporting-sync/query",
            json={"sql": "DROP TABLE doc_patient"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_query_rejects_delete(http_client: AsyncClient, mock_state):
    """DELETE is rejected."""
    async with http_client:
        response = await http_client.post(
            "/api/reporting-sync/query",
            json={"sql": "DELETE FROM doc_patient"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_query_rejects_update(http_client: AsyncClient, mock_state):
    """UPDATE is rejected."""
    async with http_client:
        response = await http_client.post(
            "/api/reporting-sync/query",
            json={"sql": "UPDATE doc_patient SET name = 'x'"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_query_rejects_truncate(http_client: AsyncClient, mock_state):
    """TRUNCATE is rejected."""
    async with http_client:
        response = await http_client.post(
            "/api/reporting-sync/query",
            json={"sql": "TRUNCATE doc_patient"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_query_allows_select_with_where(http_client: AsyncClient, mock_state):
    """SELECT with WHERE and params works."""
    pool, conn = mock_state
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock()

    async with http_client:
        response = await http_client.post(
            "/api/reporting-sync/query",
            json={
                "sql": "SELECT * FROM doc_patient WHERE country = $1",
                "params": ["CH"],
            },
        )
    assert response.status_code == 200
    data = response.json()
    assert data["row_count"] == 0
    assert data["truncated"] is False


@pytest.mark.asyncio
async def test_query_truncation(http_client: AsyncClient, mock_state):
    """Query with more rows than max_rows shows truncated=true."""
    pool, conn = mock_state

    # Return max_rows + 1 rows to trigger truncation
    rows = [{"id": str(i)} for i in range(6)]
    conn.fetch = AsyncMock(return_value=rows)
    conn.execute = AsyncMock()

    async with http_client:
        response = await http_client.post(
            "/api/reporting-sync/query",
            json={"sql": "SELECT id FROM doc_patient", "max_rows": 5},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["truncated"] is True
    assert data["row_count"] == 5


@pytest.mark.asyncio
async def test_query_no_postgres(http_client: AsyncClient):
    """Query returns 503 when PostgreSQL not connected."""
    original = state.postgres_pool
    state.postgres_pool = None
    try:
        async with http_client:
            response = await http_client.post(
                "/api/reporting-sync/query",
                json={"sql": "SELECT 1"},
            )
        assert response.status_code == 503
    finally:
        state.postgres_pool = original
