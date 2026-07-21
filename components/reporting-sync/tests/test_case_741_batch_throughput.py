"""Batch-sync throughput fixes: the per-document costs that made a restore's
auto-sync take twice as long as the restore itself.

Measured (CASE-741): 156k documents at ~199/s, with ~157s of flat inter-page
sleep and one information_schema query per document — the latter almost
always to learn the template has no sibling version tables at all (the
common case for every restored namespace). These tests pin the caller-side
fixes in _run_batch_sync; delete_from_sibling_version_tables' own semantics
are pinned by test_case_710.py and unchanged.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from reporting_sync.batch_sync import BatchSyncService
from reporting_sync.models import BatchSyncJob, BatchSyncStatus


def _doc(doc_id: str, version: int = 1, ns: str = "ct") -> dict:
    return {
        "document_id": doc_id,
        "namespace": ns,
        "template_id": "TPL-1",
        "template_version": version,
        "version": 1,
        "status": "active",
        "identity_hash": f"h-{doc_id}",
        "data": {"code": doc_id},
        "created_at": "2026-07-21T10:00:00Z",
    }


TEMPLATE = {
    "template_id": "TPL-1",
    "namespace": "ct",
    "value": "CT_TRIAL_AE",
    "version": 1,
    "fields": [{"name": "code", "type": "string", "label": "Code", "mandatory": False}],
    "reporting": {"sync_enabled": True, "sync_strategy": "latest_only"},
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
    service._resolve_template_fields = AsyncMock(side_effect=lambda t: t)
    service.schema_manager.ensure_table_for_template = AsyncMock(
        return_value="doc_ct_trial_ae__v1"
    )
    service.schema_manager.ensure_views_for_template = AsyncMock(return_value=None)
    service.schema_manager.delete_from_sibling_version_tables = AsyncMock(return_value=0)
    return service


def _job() -> BatchSyncJob:
    return BatchSyncJob(
        job_id="j1", template_value="CT_TRIAL_AE", template_id="TPL-1",
        namespace="ct", status=BatchSyncStatus.PENDING,
    )


class TestSiblingCleanupIsSkippedWithoutSiblings:
    @pytest.mark.asyncio
    async def test_single_version_table_means_zero_sibling_calls(self, svc):
        # One catalog query per namespace for the whole job — not one per
        # document — and with no sibling table on disk the delete helper is
        # never invoked at all.
        svc.schema_manager.list_version_tables = AsyncMock(
            return_value={1: "doc_ct_trial_ae__v1"}
        )
        docs = [_doc(f"d{i}") for i in range(250)]
        svc._fetch_documents = AsyncMock(return_value=(docs, len(docs)))

        job = _job()
        await svc._run_batch_sync(job, TEMPLATE, force=False, page_size=1000)

        assert job.status == BatchSyncStatus.COMPLETED
        assert job.documents_synced == 250
        svc.schema_manager.delete_from_sibling_version_tables.assert_not_awaited()
        assert svc.schema_manager.list_version_tables.await_count == 1

    @pytest.mark.asyncio
    async def test_sibling_table_present_still_cleans_per_document(self, svc):
        # The skip must not weaken the CASE-710 invariant: with a second
        # version table on disk, every document still gets its cleanup.
        svc.schema_manager.list_version_tables = AsyncMock(
            return_value={1: "doc_ct_trial_ae__v1", 2: "doc_ct_trial_ae__v2"}
        )
        docs = [_doc(f"d{i}", version=2) for i in range(5)]
        svc._fetch_documents = AsyncMock(return_value=(docs, len(docs)))
        svc.schema_manager.ensure_table_for_template = AsyncMock(
            return_value="doc_ct_trial_ae__v2"
        )

        job = _job()
        await svc._run_batch_sync(job, TEMPLATE, force=False, page_size=1000)

        assert job.status == BatchSyncStatus.COMPLETED
        deletes = svc.schema_manager.delete_from_sibling_version_tables
        assert deletes.await_count == 5
        for call in deletes.await_args_list:
            assert call.kwargs["keep_version"] == 2


class TestJobInstrumentation:
    @pytest.mark.asyncio
    async def test_phase_accumulators_and_row_count_are_recorded(self, svc):
        svc.schema_manager.list_version_tables = AsyncMock(
            return_value={1: "doc_ct_trial_ae__v1"}
        )
        docs = [_doc(f"d{i}") for i in range(10)]
        svc._fetch_documents = AsyncMock(return_value=(docs, len(docs)))

        job = _job()
        await svc._run_batch_sync(job, TEMPLATE, force=False, page_size=1000)

        # One row per document for a flat template; with flatten_arrays a
        # document is a row multiple, which is why the job reports rows too.
        assert job.rows_written == 10
        assert job.fetch_ms >= 0 and job.upsert_ms >= 0 and job.sibling_ms >= 0
        # The timers actually ran: monotonic deltas accumulate as ints.
        assert isinstance(job.fetch_ms, int)
