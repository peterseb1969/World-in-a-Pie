"""A dependency that did not answer is never read as "nothing there".

Batch sync, health, the definition syncs, the parity monitor and startup
each used to turn an unreachable store or database into an empty result,
a success or an endless retry. Each now fails, says incomplete, or reports
a deployment fault.
"""

from unittest.mock import AsyncMock, MagicMock

import asyncpg
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync import main
from reporting_sync.batch_sync import BatchSyncService, SourceUnavailable
from reporting_sync.models import BatchSyncJob, BatchSyncStatus

from .test_case_1161_run_batch_sync import _job, _template, svc  # noqa: F401  # fixture


def _unreachable(monkeypatch, module, status: int | None = None):
    def handle(request):
        if status is None:
            raise httpx.ConnectError("refused")
        return httpx.Response(status, json={})

    real = httpx.AsyncClient
    monkeypatch.setattr(module.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handle), **k))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, 503])
async def test_an_unreadable_document_store_fails_the_job(svc, monkeypatch, status):  # noqa: F811
    from reporting_sync import batch_sync

    _unreachable(monkeypatch, batch_sync, status)
    with pytest.raises(SourceUnavailable):
        await svc._fetch_documents("TPL-1", 1, 1)


@pytest.mark.asyncio
async def test_a_force_run_drops_nothing_while_the_source_cannot_answer(svc):  # noqa: F811
    svc._fetch_documents = AsyncMock(side_effect=SourceUnavailable("document-store down"))
    svc.schema_manager.drop_relations_for_template = AsyncMock()
    job = _job()
    await svc._run_batch_sync(job, _template(1, ["a"], strategy="latest_only"), force=True, page_size=10)
    assert job.status == BatchSyncStatus.FAILED
    assert "document-store down" in job.error_message
    svc.schema_manager.drop_relations_for_template.assert_not_called()


@pytest.mark.asyncio
async def test_a_force_run_with_no_documents_rebuilds_what_it_dropped(svc):  # noqa: F811
    svc._fetch_documents = AsyncMock(return_value=([], 0, None))
    svc.schema_manager.drop_relations_for_template = AsyncMock(return_value=["ct.doc_ct_x"])
    job = _job()
    await svc._run_batch_sync(job, _template(1, ["a"], strategy="latest_only"), force=True, page_size=10)
    assert job.status == BatchSyncStatus.COMPLETED
    svc.schema_manager.ensure_views_for_template.assert_awaited()


@pytest.mark.asyncio
async def test_a_page_that_cannot_be_read_fails_the_job_midway(svc):  # noqa: F811
    calls = {"n": 0}

    async def fetch(template_id, page, page_size, status="active", namespace=None, cursor=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"document_id": "d1"}], 5, None
        raise SourceUnavailable("page 2 refused")

    svc._fetch_documents = AsyncMock(side_effect=fetch)
    job = _job()
    await svc._run_batch_sync(job, _template(1, ["a"]), force=False, page_size=10)
    assert job.status == BatchSyncStatus.FAILED


@pytest.mark.asyncio
async def test_a_template_listing_that_fails_is_503(monkeypatch):
    from reporting_sync import batch_sync

    service = BatchSyncService(MagicMock())
    _unreachable(monkeypatch, batch_sync, 500)
    with pytest.raises(SourceUnavailable):
        await service._list_templates()
    monkeypatch.setattr(main.state, "batch_sync_service", service)
    transport = ASGITransport(app=main.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"X-API-Key": "rs_test_admin_key"}) as c:
        r = await c.post("/api/reporting-sync/sync/batch")
    assert r.status_code == 503


@pytest.mark.parametrize(
    "result,status",
    [
        ({"synced": 3, "failed": 0, "complete": True}, "completed"),
        ({"synced": 3, "failed": 1, "complete": True}, "completed_with_errors"),
        ({"synced": 0, "failed": 0, "complete": False}, "incomplete"),
    ],
)
def test_a_definition_sync_says_how_it_ended(result, status):
    assert main._definition_sync_status(result) == status


@pytest.mark.asyncio
async def test_health_is_503_with_neither_nats_nor_postgres(monkeypatch):
    monkeypatch.setattr(main, "_postgres_live_check", AsyncMock(return_value=False))
    monkeypatch.setattr(type(main.state), "nats_client", property(lambda self: None))
    transport = ASGITransport(app=main.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/health")
    assert (r.status_code, r.json()["status"]) == (503, "unhealthy")


@pytest.mark.asyncio
async def test_a_bookkeeping_repair_without_privileges_is_a_deployment_fault(monkeypatch):
    conn = AsyncMock()
    monkeypatch.setattr(main.settings, "postgres_owner_password", "o")
    monkeypatch.setattr(main.settings, "postgres_read_password", "r")
    monkeypatch.setattr(main.asyncpg, "connect", AsyncMock(return_value=conn))
    monkeypatch.setattr(main, "repair_bookkeeping_location",
                        AsyncMock(side_effect=asyncpg.InsufficientPrivilegeError("no")))
    with pytest.raises(main.ServiceRolesError):
        await main.connect_postgres()
    conn.close.assert_awaited()


@pytest.mark.parametrize(
    "result,failure",
    [
        ({"synced": 2, "failed": 0, "complete": True}, None),
        ({"synced": 2, "failed": 1, "complete": True}, "1 item(s) failed"),
        ({"synced": 0, "failed": 0, "complete": False}, "the source listing could not be read to the end"),
        (BatchSyncJob(job_id="j", template_value="X", status=BatchSyncStatus.FAILED,
                      error_message="Template X not found"), "Template X not found"),
        (BatchSyncJob(job_id="j", template_value="X", status=BatchSyncStatus.PENDING), None),
        (None, None),
    ],
)
def test_a_parity_repair_is_judged_by_its_result(result, failure):
    from reporting_sync.parity_monitor import _repair_failure

    assert _repair_failure(result) == failure


@pytest.mark.asyncio
async def test_each_version_is_written_with_its_own_reporting_config(svc):  # noqa: F811
    """v1 is all_versions without metadata, v2 latest_only: a v1 document's
    row and upsert follow v1's config, the shape its table was built from."""
    v1 = _template(1, ["a"], strategy="all_versions")
    v1["reporting"]["include_metadata"] = False
    v2 = _template(2, ["a"], strategy="latest_only")
    svc._fetch_template_version = AsyncMock(return_value=v1)
    from .test_case_1161_run_batch_sync import _doc, _store

    svc._fetch_documents = _store([_doc(1, 1, {"a": "x"})])
    job = _job()
    await svc._run_batch_sync(job, v2, force=False, page_size=10)
    sql = next(c.args[0] for c in svc._conn.execute.await_args_list if "INSERT INTO" in c.args[0])
    assert "ON CONFLICT (document_id, version)" in sql  # v1's all_versions shape
    assert '"created_at"' not in sql  # v1 keeps no metadata columns
    assert job.documents_synced == 1
