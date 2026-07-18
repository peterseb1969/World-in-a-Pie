"""The templates metadata table is rebuildable and discoverable (CASE-645).

The templates table was kept current by live template events only: no
rebuild/backfill route, no startup backfill, and absent from both the
/tables and CSV-export allow-lists — so a blue-green reporting rebuild
silently lost all template metadata until each template's next write, and
even a populated table was unlistable/unexportable. Same rebuild-loss shape
as term relations, fixed the same way.

Pins:
1. POST /sync/batch/templates routes to the template-metadata handler (and
   is not swallowed by the /sync/batch/{template_value} wildcard).
2. _initial_metadata_sync backfills templates alongside the other three
   metadata tables, with no namespace argument (all-namespace sweep).
3. Both allow-lists admit "templates": /tables listing and CSV export.
4. The backfill fetches latest_only=true — the table keys one row per
   (namespace, template_id), so without it the final upserted row would
   depend on page order across versions, not on which version is latest.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync.batch_sync import BatchSyncService
from reporting_sync.main import _is_allowed_table, app, state


def _mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm
    return pool, conn


@pytest.fixture
def mock_state():
    pool, conn = _mock_pool()
    original_pool = state.postgres_pool
    state.postgres_pool = pool
    yield pool, conn
    state.postgres_pool = original_pool


@pytest.fixture
def http_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def mock_batch_service():
    mock_svc = AsyncMock(spec=BatchSyncService)
    original = state.batch_sync_service
    state.batch_sync_service = mock_svc
    yield mock_svc
    state.batch_sync_service = original


@pytest.mark.asyncio
async def test_batch_templates_route(http_client: AsyncClient, mock_batch_service):
    """POST /sync/batch/templates hits the template-metadata handler,
    not the {template_value} wildcard."""
    mock_batch_service.batch_sync_templates = AsyncMock(
        return_value={"synced": 7, "failed": 0, "total": 7}
    )

    async with http_client:
        resp = await http_client.post("/api/reporting-sync/sync/batch/templates?namespace=wip")

    assert resp.status_code == 200
    data = resp.json()
    assert data["table"] == "templates"
    assert data["synced"] == 7
    mock_batch_service.batch_sync_templates.assert_awaited_once_with(
        namespace="wip", page_size=100,
    )


@pytest.mark.asyncio
async def test_initial_sync_includes_templates():
    """Startup backfill sweeps templates too, with no namespace argument."""
    from reporting_sync.main import _initial_metadata_sync

    batch_service = MagicMock()
    batch_service.batch_sync_terminologies = AsyncMock(return_value={"synced": 1})
    batch_service.batch_sync_terms = AsyncMock(return_value={"synced": 1})
    batch_service.batch_sync_term_relations = AsyncMock(return_value={"synced": 1})
    batch_service.batch_sync_templates = AsyncMock(return_value={"synced": 1})

    with patch("reporting_sync.main.retry_async", new=AsyncMock()):
        await _initial_metadata_sync(batch_service)

    batch_service.batch_sync_templates.assert_awaited_once_with()


def test_templates_in_export_allowlist():
    """CSV export admits the templates metadata table."""
    assert _is_allowed_table("templates") is True


@pytest.mark.asyncio
async def test_templates_in_tables_listing(http_client: AsyncClient, mock_state):
    """GET /tables includes a templates table instead of filtering it out."""
    _pool, conn = mock_state
    conn.fetch = AsyncMock(side_effect=[
        [
            {"table_schema": "wip", "table_name": "templates"},
            {"table_schema": "wip", "table_name": "_wip_schema_migrations"},
        ],
        [{"column_name": "template_id", "data_type": "text", "is_nullable": "NO"}],
    ])
    conn.fetchval = AsyncMock(return_value=3)

    async with http_client:
        response = await http_client.get("/api/reporting-sync/tables")

    assert response.status_code == 200
    table_names = [t["name"] for t in response.json()["tables"]]
    assert "templates" in table_names
    assert "_wip_schema_migrations" not in table_names


@pytest.mark.asyncio
async def test_backfill_requests_latest_only():
    """batch_sync_templates asks the template-store for latest versions only."""
    service = BatchSyncService.__new__(BatchSyncService)
    service.schema_manager = AsyncMock()
    service.pool = _mock_pool()[0]

    captured: dict = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"items": [], "pages": 1}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None, headers=None):
            captured["params"] = params
            return FakeResponse()

    with patch("reporting_sync.batch_sync.httpx.AsyncClient", return_value=FakeClient()):
        await service.batch_sync_templates()

    assert captured["params"]["latest_only"] == "true"
