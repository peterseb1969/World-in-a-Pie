"""The eager table-ensure targets the latest ACTIVE version (CASE-766).

The batch sync's instance-wide template listing is deliberately status-free
(a fully-deactivated template's documents must still sync), so the listed
row can be an INACTIVE newest version — a normal state: deactivating a
version leaves documents pinned to it. The eager pre-document ensure used
to build THAT version's table, while the restore gate's structural parity
demands the latest-ACTIVE version's table (the one a new write lands on).
The mismatch halted a real restore at the 30s gate with "table missing" —
stranding the namespace half-restored.

Pinned here: the eager ensure resolves the latest active version when the
listed row is inactive; skips eagerly when no version is active (parity's
active-only listing skips the template too); and the lazy per-document
materialization for inactive-pinned documents is untouched.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from reporting_sync.batch_sync import BatchSyncService
from reporting_sync.models import BatchSyncJob, BatchSyncStatus


def _doc(doc_id: str, version: int = 2, ns: str = "kb") -> dict:
    return {
        "document_id": doc_id,
        "namespace": ns,
        "template_id": "TPL-CR",
        "template_version": version,
        "version": 1,
        "status": "active",
        "identity_hash": f"h-{doc_id}",
        "data": {"code": doc_id},
        "created_at": "2026-07-22T10:00:00Z",
    }


def _template(version: int, status: str) -> dict:
    return {
        "template_id": "TPL-CR",
        "namespace": "kb",
        "value": "CASE_RECORD",
        "version": version,
        "status": status,
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
        side_effect=lambda ns, t: f"doc_case_record__v{t.get('version')}"
    )
    service.schema_manager.ensure_views_for_template = AsyncMock(return_value=None)
    service.schema_manager.delete_from_sibling_version_tables = AsyncMock(return_value=0)
    service.schema_manager.list_version_tables = AsyncMock(return_value={})
    service.schema_manager.drop_relations_for_template = AsyncMock(return_value=[])
    return service


def _job() -> BatchSyncJob:
    return BatchSyncJob(
        job_id="j1", template_value="CASE_RECORD", template_id="TPL-CR",
        namespace="kb", status=BatchSyncStatus.PENDING,
    )


class TestEagerEnsureIsActiveAware:
    @pytest.mark.asyncio
    async def test_inactive_latest_resolves_the_active_version(self, svc):
        # Listed row is v2-inactive; the eager ensure must build v1's table
        # (the latest ACTIVE — what parity demands), not v2's.
        svc._fetch_latest_active_version = AsyncMock(
            return_value=_template(1, "active")
        )
        svc._fetch_documents = AsyncMock(return_value=([], 0))

        job = _job()
        await svc._run_batch_sync(job, _template(2, "inactive"), force=False, page_size=1000)

        assert job.status == BatchSyncStatus.COMPLETED
        svc._fetch_latest_active_version.assert_awaited_once_with("CASE_RECORD", "kb")
        ensure = svc.schema_manager.ensure_table_for_template
        ensure.assert_awaited_once()
        ensured_template = ensure.await_args.args[1]
        assert ensured_template["version"] == 1
        assert ensured_template["status"] == "active"

    @pytest.mark.asyncio
    async def test_active_latest_never_refetches(self, svc):
        svc._fetch_latest_active_version = AsyncMock()
        svc._fetch_documents = AsyncMock(return_value=([], 0))

        job = _job()
        await svc._run_batch_sync(job, _template(2, "active"), force=False, page_size=1000)

        svc._fetch_latest_active_version.assert_not_awaited()
        svc.schema_manager.ensure_table_for_template.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_active_version_skips_eager_ensure(self, svc):
        # Every version inactive: parity's active-only listing skips the
        # template, so the eager ensure must too — no table, clean complete.
        svc._fetch_latest_active_version = AsyncMock(return_value=None)
        svc._fetch_documents = AsyncMock(return_value=([], 0))

        job = _job()
        await svc._run_batch_sync(job, _template(2, "inactive"), force=False, page_size=1000)

        assert job.status == BatchSyncStatus.COMPLETED
        svc.schema_manager.ensure_table_for_template.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_docs_pinned_to_inactive_version_still_materialize_lazily(self, svc):
        # The lazy per-page path is status-independent and must stay so:
        # documents pinned to the inactive v2 get v2's table at sync time,
        # alongside the eagerly ensured active v1 table.
        svc._fetch_latest_active_version = AsyncMock(
            return_value=_template(1, "active")
        )
        docs = [_doc("d1", version=2), _doc("d2", version=2)]
        svc._fetch_documents = AsyncMock(return_value=(docs, len(docs)))

        job = _job()
        await svc._run_batch_sync(job, _template(2, "inactive"), force=False, page_size=1000)

        assert job.status == BatchSyncStatus.COMPLETED
        assert job.documents_synced == 2
        ensured_versions = sorted(
            call.args[1]["version"]
            for call in svc.schema_manager.ensure_table_for_template.await_args_list
        )
        assert ensured_versions == [1, 2]
