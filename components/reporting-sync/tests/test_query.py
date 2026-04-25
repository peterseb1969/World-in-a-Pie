"""Tests for reporting query and batch sync endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync.batch_sync import BatchSyncService
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
    _pool, conn = mock_state

    # Mock information_schema.tables query
    conn.fetch = AsyncMock(side_effect=[
        # First call: list all tables
        [
            {"table_name": "doc_patient"},
            {"table_name": "doc_bank_transaction"},
            {"table_name": "terminologies"},
            {"table_name": "terms"},
            {"table_name": "term_relations"},
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
    _pool, conn = mock_state

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
    _pool, conn = mock_state

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
    _pool, conn = mock_state

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
    _pool, conn = mock_state
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
    _pool, conn = mock_state

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


# =========================================================================
# CSV Export
# =========================================================================


def _mock_prepared_stmt(columns, rows):
    """Create a mock asyncpg PreparedStatement with given columns and rows.

    Uses MagicMock (not AsyncMock) so sync methods like get_attributes() work.
    """
    attrs = []
    for col in columns:
        attr = MagicMock()
        attr.name = col
        attrs.append(attr)

    stmt = MagicMock()
    stmt.get_attributes.return_value = attrs

    async def cursor(*args):
        for row in rows:
            yield row

    stmt.cursor = cursor
    return stmt


def _setup_csv_mocks(conn, columns, rows):
    """Wire up conn mocks for CSV export tests."""
    stmt = _mock_prepared_stmt(columns, rows)
    conn.prepare = AsyncMock(return_value=stmt)
    conn.execute = AsyncMock()
    # asyncpg's transaction() is a sync method returning an async context manager
    txn = MagicMock()
    txn.__aenter__ = AsyncMock()
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)


@pytest.mark.asyncio
async def test_export_table_csv(http_client: AsyncClient, mock_state):
    """GET /export/csv?table=doc_patient streams CSV."""
    _pool, conn = mock_state
    _setup_csv_mocks(conn, ["id", "name"], [
        {"id": "1", "name": "Alice"},
        {"id": "2", "name": "Bob"},
    ])

    async with http_client:
        response = await http_client.get("/api/reporting-sync/export/csv?table=doc_patient")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "doc_patient.csv" in response.headers["content-disposition"]
    lines = [line.strip() for line in response.text.strip().split("\n")]
    assert lines[0] == "id,name"
    assert lines[1] == "1,Alice"
    assert lines[2] == "2,Bob"


@pytest.mark.asyncio
async def test_export_table_csv_rejects_disallowed_table(http_client: AsyncClient, mock_state):
    """GET /export/csv with a non-allowed table returns 400."""
    async with http_client:
        response = await http_client.get("/api/reporting-sync/export/csv?table=_wip_secret")
    assert response.status_code == 400
    assert "not available" in response.json()["detail"]


@pytest.mark.asyncio
async def test_export_table_csv_allows_metadata_tables(http_client: AsyncClient, mock_state):
    """GET /export/csv allows terminologies, terms, term_relations."""
    _pool, conn = mock_state
    _setup_csv_mocks(conn, ["id"], [{"id": "t1"}])

    async with http_client:
        response = await http_client.get("/api/reporting-sync/export/csv?table=terminologies")

    assert response.status_code == 200
    assert "terminologies.csv" in response.headers["content-disposition"]


@pytest.mark.asyncio
async def test_export_query_csv(http_client: AsyncClient, mock_state):
    """POST /export/csv streams CSV from a SQL query."""
    _pool, conn = mock_state
    _setup_csv_mocks(conn, ["count"], [{"count": 42}])

    async with http_client:
        response = await http_client.post(
            "/api/reporting-sync/export/csv",
            json={"sql": "SELECT COUNT(*) as count FROM doc_patient"},
        )

    assert response.status_code == 200
    lines = [line.strip() for line in response.text.strip().split("\n")]
    assert lines[0] == "count"
    assert lines[1] == "42"


@pytest.mark.asyncio
async def test_export_query_csv_rejects_write(http_client: AsyncClient, mock_state):
    """POST /export/csv rejects write SQL."""
    async with http_client:
        response = await http_client.post(
            "/api/reporting-sync/export/csv",
            json={"sql": "DELETE FROM doc_patient"},
        )
    assert response.status_code == 400
    assert "read-only" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_export_csv_no_postgres(http_client: AsyncClient):
    """Export returns 503 when PostgreSQL not connected."""
    original = state.postgres_pool
    state.postgres_pool = None
    try:
        async with http_client:
            response = await http_client.get("/api/reporting-sync/export/csv?table=doc_patient")
        assert response.status_code == 503
    finally:
        state.postgres_pool = original


@pytest.mark.asyncio
async def test_export_query_csv_custom_filename(http_client: AsyncClient, mock_state):
    """POST /export/csv respects custom filename."""
    _pool, conn = mock_state
    _setup_csv_mocks(conn, ["x"], [])

    async with http_client:
        response = await http_client.post(
            "/api/reporting-sync/export/csv",
            json={"sql": "SELECT 1 as x", "filename": "my-report.csv"},
        )

    assert response.status_code == 200
    assert "my-report.csv" in response.headers["content-disposition"]


# =========================================================================
# Batch Sync Endpoint Routing
# =========================================================================
# These tests verify that literal routes (/sync/batch/terminologies, etc.)
# are not swallowed by the {template_value} wildcard route.


@pytest.fixture
def mock_batch_service():
    """Patch state.batch_sync_service with a mock."""
    mock_svc = AsyncMock(spec=BatchSyncService)
    original = state.batch_sync_service
    state.batch_sync_service = mock_svc
    yield mock_svc
    state.batch_sync_service = original


@pytest.mark.asyncio
async def test_batch_terminologies_route(http_client: AsyncClient, mock_batch_service):
    """POST /sync/batch/terminologies hits the terminology handler, not {template_value}."""
    mock_batch_service.batch_sync_terminologies = AsyncMock(
        return_value={"synced": 5, "failed": 0, "total": 5}
    )

    async with http_client:
        resp = await http_client.post("/api/reporting-sync/sync/batch/terminologies?namespace=wip")

    assert resp.status_code == 200
    data = resp.json()
    # The terminology endpoint returns {"status": "completed", "table": "terminologies", ...}
    assert data["table"] == "terminologies"
    assert data["synced"] == 5
    mock_batch_service.batch_sync_terminologies.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_terms_route(http_client: AsyncClient, mock_batch_service):
    """POST /sync/batch/terms hits the term handler, not {template_value}."""
    mock_batch_service.batch_sync_terms = AsyncMock(
        return_value={"synced": 42, "failed": 0, "total": 42}
    )

    async with http_client:
        resp = await http_client.post("/api/reporting-sync/sync/batch/terms?namespace=wip")

    assert resp.status_code == 200
    data = resp.json()
    assert data["table"] == "terms"
    assert data["synced"] == 42
    mock_batch_service.batch_sync_terms.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_relations_route(http_client: AsyncClient, mock_batch_service):
    """POST /sync/batch/relations hits the relation handler, not {template_value}."""
    mock_batch_service.batch_sync_term_relations = AsyncMock(
        return_value={"synced": 99, "failed": 0, "total": 99}
    )

    async with http_client:
        resp = await http_client.post("/api/reporting-sync/sync/batch/relations?namespace=wip")

    assert resp.status_code == 200
    data = resp.json()
    assert data["table"] == "term_relations"
    assert data["synced"] == 99
    mock_batch_service.batch_sync_term_relations.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_template_value_route(http_client: AsyncClient, mock_batch_service):
    """POST /sync/batch/person hits the {template_value} handler."""
    from reporting_sync.models import BatchSyncJob, BatchSyncStatus

    job = BatchSyncJob(
        job_id="test-123",
        template_value="person",
        status=BatchSyncStatus.RUNNING,
    )
    mock_batch_service.start_batch_sync = AsyncMock(return_value=job)

    async with http_client:
        resp = await http_client.post("/api/reporting-sync/sync/batch/person")

    assert resp.status_code == 200
    data = resp.json()
    assert data["template_value"] == "person"
    mock_batch_service.start_batch_sync.assert_awaited_once_with(
        template_value="person", force=False, page_size=100,
    )


@pytest.mark.asyncio
async def test_batch_no_service_returns_503(http_client: AsyncClient):
    """Batch endpoints return 503 when batch_sync_service is None."""
    original = state.batch_sync_service
    state.batch_sync_service = None
    try:
        async with http_client:
            resp = await http_client.post("/api/reporting-sync/sync/batch/terminologies?namespace=wip")
        assert resp.status_code == 503
    finally:
        state.batch_sync_service = original
