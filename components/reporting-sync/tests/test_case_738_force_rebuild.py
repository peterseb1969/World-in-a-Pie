"""Force = drop-and-rebuild for the document batch sync.

The `force` parameter was accepted and threaded for its whole life without
ever being read — a parameter that does nothing teaches callers a false
model. It now means: drop the template's existing reporting relations in
the target namespace (version tables, entity views, any legacy pre-split
table) before the sync rebuilds them from source. Upserts cannot heal
mis-shaped DDL — a table created from a same-valued foreign template
rejects the namespace's own documents; only drop-and-recreate recovers.

Pinned here: the namespace requirement (table names derive from the
template VALUE, so an instance-wide drop could destroy same-valued foreign
templates' tables), the scope guard (only the owning namespace's job
drops), and the dedup interaction (an active job wins over force, loudly —
never silently). The drop DDL itself is pinned in test_integration.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync.batch_sync import BatchSyncService
from reporting_sync.main import app, state
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
    service.schema_manager.list_version_tables = AsyncMock(
        return_value={1: "doc_ct_trial_ae__v1"}
    )
    service.schema_manager.drop_relations_for_template = AsyncMock(
        return_value=["ct.doc_ct_trial_ae__entities", "ct.doc_ct_trial_ae__v1"]
    )
    return service


def _job(ns: str | None = "ct") -> BatchSyncJob:
    return BatchSyncJob(
        job_id="j1", template_value="CT_TRIAL_AE", template_id="TPL-1",
        namespace=ns, status=BatchSyncStatus.PENDING,
    )


class TestForceDropsBeforeSync:
    @pytest.mark.asyncio
    async def test_force_drops_then_recreates(self, svc):
        # The drop precedes any table-ensure: recreating from current
        # template shapes only recovers mis-shaped DDL if the old relations
        # are gone first.
        order: list[str] = []
        svc.schema_manager.drop_relations_for_template = AsyncMock(
            side_effect=lambda *a, **k: order.append("drop") or ["ct.doc_ct_trial_ae__v1"]
        )
        ensure = svc.schema_manager.ensure_table_for_template
        ensure.side_effect = (
            lambda *a, **k: order.append("ensure") or "doc_ct_trial_ae__v1"
        )
        docs = [_doc(f"d{i}") for i in range(3)]
        svc._fetch_documents = AsyncMock(return_value=(docs, len(docs)))

        job = _job()
        await svc._run_batch_sync(job, TEMPLATE, force=True, page_size=1000)

        assert job.status == BatchSyncStatus.COMPLETED
        assert order[0] == "drop"
        assert "ensure" in order
        drop = svc.schema_manager.drop_relations_for_template
        assert drop.await_count == 1
        args = drop.await_args.args
        assert args[0] == "ct" and args[1] == "CT_TRIAL_AE"

    @pytest.mark.asyncio
    async def test_dropped_relations_recorded_on_job(self, svc):
        # A destructive step must be auditable from the job record.
        docs = [_doc("d1")]
        svc._fetch_documents = AsyncMock(return_value=(docs, 1))

        job = _job()
        await svc._run_batch_sync(job, TEMPLATE, force=True, page_size=1000)

        assert job.dropped_relations == [
            "ct.doc_ct_trial_ae__entities", "ct.doc_ct_trial_ae__v1",
        ]

    @pytest.mark.asyncio
    async def test_without_force_nothing_is_dropped(self, svc):
        docs = [_doc("d1")]
        svc._fetch_documents = AsyncMock(return_value=(docs, 1))

        job = _job()
        await svc._run_batch_sync(job, TEMPLATE, force=False, page_size=1000)

        svc.schema_manager.drop_relations_for_template.assert_not_awaited()
        assert job.dropped_relations == []

    @pytest.mark.asyncio
    async def test_foreign_template_job_never_drops(self, svc):
        # Batch-all fans out over the instance-wide template list. Table
        # names derive from the template VALUE, so a foreign same-valued
        # template's job dropping in the target namespace would race the
        # owning template's rebuild of identically-named tables. The guard
        # mirrors the eager-ensure guard: only the owning namespace's job
        # drops.
        foreign = dict(TEMPLATE, namespace="kb")
        docs = [_doc("d1")]
        svc._fetch_documents = AsyncMock(return_value=(docs, 1))

        job = _job(ns="ct")
        await svc._run_batch_sync(job, foreign, force=True, page_size=1000)

        svc.schema_manager.drop_relations_for_template.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_force_with_zero_documents_still_drops(self, svc):
        # "Rebuild from source" with an empty source degrades to an empty
        # table, not resurrection of the dropped content — the drop must not
        # be skipped just because nothing will be fetched.
        svc._fetch_documents = AsyncMock(return_value=([], 0))

        job = _job()
        await svc._run_batch_sync(job, TEMPLATE, force=True, page_size=1000)

        assert job.status == BatchSyncStatus.COMPLETED
        svc.schema_manager.drop_relations_for_template.assert_awaited_once()
        # The eager ensure recreates the latest version's table (and its
        # views, via ensure_table_for_template's create path).
        svc.schema_manager.ensure_table_for_template.assert_awaited_once()


class TestForceRequiresNamespace:
    @pytest.mark.asyncio
    async def test_service_rejects_force_without_namespace(self, svc):
        with pytest.raises(ValueError, match="requires an explicit namespace"):
            await svc.start_batch_sync(
                "CT_TRIAL_AE", force=True, namespace=None, template=TEMPLATE
            )


class TestForceVsDedup:
    @pytest.mark.asyncio
    async def test_active_job_wins_and_is_marked_deduplicated(self, svc):
        # Force never cancels an active job — the dedup is a correctness
        # guard against two concurrent writers on one table. The existing
        # job comes back flagged so the trigger response can say force was
        # NOT applied instead of silently joining the non-dropping run.
        active = _job()
        active.status = BatchSyncStatus.RUNNING
        svc._jobs[active.job_id] = active

        returned = await svc.start_batch_sync(
            "CT_TRIAL_AE", force=True, namespace="ct", template=TEMPLATE
        )

        assert returned is active
        assert returned.deduplicated is True
        svc.schema_manager.drop_relations_for_template.assert_not_awaited()


# =========================================================================
# Route layer
# =========================================================================


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
async def test_single_route_force_without_namespace_is_400(
    http_client: AsyncClient, mock_batch_service
):
    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/sync/batch/person?force=true"
        )
    assert resp.status_code == 400
    assert "namespace" in resp.json()["detail"]
    mock_batch_service.start_batch_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_all_route_force_without_namespace_is_400(
    http_client: AsyncClient, mock_batch_service
):
    async with http_client:
        resp = await http_client.post("/api/reporting-sync/sync/batch?force=true")
    assert resp.status_code == 400
    assert "namespace" in resp.json()["detail"]
    mock_batch_service.start_batch_sync_all.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_trigger_message_announces_rebuild(
    http_client: AsyncClient, mock_batch_service
):
    job = BatchSyncJob(
        job_id="j1", template_value="person", namespace="ct",
        status=BatchSyncStatus.PENDING,
    )
    mock_batch_service.start_batch_sync = AsyncMock(return_value=job)

    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/sync/batch/person?force=true&namespace=ct"
        )

    assert resp.status_code == 200
    assert "Force rebuild started" in resp.json()["message"]
    mock_batch_service.start_batch_sync.assert_awaited_once_with(
        template_value="person", force=True, page_size=1000, namespace="ct",
    )


@pytest.mark.asyncio
async def test_force_on_deduplicated_job_says_not_applied(
    http_client: AsyncClient, mock_batch_service
):
    # The one message that must never be soft: a force trigger that landed
    # on an active job did NOT drop anything, and the caller has to learn
    # that from the response, not from missing tables later.
    job = BatchSyncJob(
        job_id="j1", template_value="person", namespace="ct",
        status=BatchSyncStatus.RUNNING, deduplicated=True,
    )
    mock_batch_service.start_batch_sync = AsyncMock(return_value=job)

    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/sync/batch/person?force=true&namespace=ct"
        )

    assert resp.status_code == 200
    message = resp.json()["message"]
    assert "force NOT applied" in message
    assert "j1" in message
