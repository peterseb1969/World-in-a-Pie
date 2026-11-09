"""A template's batch sync job, pinned before _run_batch_sync's refactor.

Provenance: CASE-1161. The suite never reached these branches: a template
with sync disabled completes at once; a document pinned to an older,
fetchable template version is ensured and transformed against that
version; a document that fails to sync is counted and the job goes on;
all_versions runs no sibling cleanup; a view warning is kept on the job;
an eager ensure that yields no table leaves the empty exit naming no table;
a cancelled job is CANCELLED.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from reporting_sync.batch_sync import BatchSyncService
from reporting_sync.models import BatchSyncJob, BatchSyncStatus


def _template(version: int, fields: list[str], strategy: str = "all_versions", enabled: bool = True) -> dict:
    return {
        "template_id": "TPL-1", "namespace": "ct", "value": "CT_X", "version": version,
        "fields": [{"name": f, "type": "string", "label": f} for f in fields],
        "reporting": {"sync_enabled": enabled, "sync_strategy": strategy},
    }


def _doc(i: int, tv: int, data: dict) -> dict:
    return {
        "document_id": f"d{i}", "namespace": "ct", "template_id": "TPL-1",
        "template_version": tv, "version": 1, "status": "active",
        "identity_hash": f"h{i}", "data": data,
    }


@pytest.fixture
def svc():
    pool = MagicMock()
    conn = AsyncMock()
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acm)
    service = BatchSyncService(pool)
    service._conn = conn
    sm = service.schema_manager
    sm.ensure_table_for_template = AsyncMock(side_effect=lambda ns, t, cached: f"t_v{t['version']}")
    sm.ensure_views_for_template = AsyncMock(return_value="legacy table doc_ct_x kept")
    sm.list_version_tables = AsyncMock(return_value={1: "t_v1", 2: "t_v2"})
    sm.delete_from_sibling_version_tables = AsyncMock()
    return service


def _job() -> BatchSyncJob:
    return BatchSyncJob(job_id="j1", template_value="CT_X", template_id="TPL-1",
                        namespace="ct", status=BatchSyncStatus.PENDING)


def _store(docs: list[dict]):
    async def fetch(template_id, page, page_size, status="active", namespace=None, cursor=None):
        if cursor is None:
            return docs[:1], len(docs), None
        return (docs, -1, None) if cursor != "done" else ([], -1, None)
    return AsyncMock(side_effect=fetch)


@pytest.mark.asyncio
async def test_sync_disabled_completes_at_once(svc):
    svc._fetch_documents = AsyncMock()
    job = _job()
    await svc._run_batch_sync(job, _template(1, ["a"], enabled=False), force=False, page_size=10)
    assert job.status == BatchSyncStatus.COMPLETED
    assert job.error_message == "Sync disabled for this template"
    svc._fetch_documents.assert_not_called()


@pytest.mark.asyncio
async def test_pinned_versions_failures_all_versions_and_view_warning(svc):
    v1 = _template(1, ["old"])
    svc._fetch_template_version = AsyncMock(return_value=v1)
    broken = _doc(3, 2, {"new": "z"})
    del broken["document_id"]
    svc._fetch_documents = _store([_doc(1, 1, {"old": "x"}), _doc(2, 2, {"new": "y"}), broken])

    job = _job()
    await svc._run_batch_sync(job, _template(2, ["new"]), force=False, page_size=10)

    assert job.status == BatchSyncStatus.COMPLETED
    assert (job.total_documents, job.documents_synced, job.documents_failed, job.rows_written) == (3, 2, 1, 2)
    svc._fetch_template_version.assert_awaited_once_with("TPL-1", 1)
    ensured = [c.args[1]["version"] for c in svc.schema_manager.ensure_table_for_template.await_args_list]
    assert ensured == [2, 1]
    tables = [c.args[0].split()[2] for c in svc._conn.execute.await_args_list]
    assert tables == ["t_v1", "t_v2"]
    old_sql = svc._conn.execute.await_args_list[0].args[0]
    assert '"old"' in old_sql and '"new"' not in old_sql
    svc.schema_manager.delete_from_sibling_version_tables.assert_not_called()
    assert job.error_message == "legacy table doc_ct_x kept"


@pytest.mark.asyncio
async def test_no_eager_table_leaves_the_empty_exit_naming_none(svc, caplog):
    svc.schema_manager.ensure_table_for_template = AsyncMock(return_value=None)
    svc._fetch_documents = _store([])
    job = _job()
    with caplog.at_level("INFO"):
        await svc._run_batch_sync(job, _template(1, ["a"]), force=False, page_size=10)
    assert job.status == BatchSyncStatus.COMPLETED and job.total_documents == 0
    assert "foreign template — no table created in scope" in caplog.text


@pytest.mark.asyncio
async def test_a_cancelled_job_is_cancelled(svc):
    svc._fetch_documents = AsyncMock(side_effect=asyncio.CancelledError())
    job = _job()
    await svc._run_batch_sync(job, _template(1, ["a"]), force=False, page_size=10)
    assert job.status == BatchSyncStatus.CANCELLED and job.completed_at is not None
