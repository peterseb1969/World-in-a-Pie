"""reporting-sync reads templates resolved by Template Store, never by itself.

Template Store's resolving routes are the one inheritance resolver: they pin
the parent version, keep the child's own properties (its reporting config
among them) and refuse a broken chain. reporting-sync used to merge parent
fields itself, from the parent's latest version and silently falling back to
the child when the parent was missing. Where it gets a template from a
listing (served as stored), it now re-reads an extending template through the
by-id route instead.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reporting_sync import parity
from reporting_sync.batch_sync import BatchSyncService
from reporting_sync.models import BatchSyncJob, BatchSyncStatus

STORED = {
    "template_id": "TPL-CHILD", "namespace": "kb", "value": "CHILD", "version": 3,
    "status": "active", "extends": "TPL-PARENT", "extends_version": 1,
    "fields": [{"name": "own", "type": "string", "label": "Own"}],
    "reporting": {"sync_enabled": True},
}
RESOLVED = {
    **STORED,
    "fields": [
        {"name": "inherited", "type": "string", "label": "Inherited"},
        {"name": "own", "type": "string", "label": "Own"},
    ],
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
    service.schema_manager.ensure_table_for_template = AsyncMock(return_value="doc_child__v3")
    service.schema_manager.ensure_views_for_template = AsyncMock(return_value=None)
    service.schema_manager.delete_from_sibling_version_tables = AsyncMock(return_value=0)
    service.schema_manager.list_version_tables = AsyncMock(return_value={})
    service.schema_manager.drop_relations_for_template = AsyncMock(return_value=[])
    service._fetch_documents = AsyncMock(return_value=([], 0, None))
    return service


def _job() -> BatchSyncJob:
    return BatchSyncJob(
        job_id="j1", template_value="CHILD", template_id="TPL-CHILD",
        namespace="kb", status=BatchSyncStatus.PENDING,
    )


@pytest.mark.asyncio
async def test_a_listed_extending_template_is_reread_resolved(svc):
    svc._fetch_template_version = AsyncMock(return_value=RESOLVED)
    job = _job()

    await svc._run_batch_sync(job, dict(STORED), force=False, page_size=100)

    assert job.status == BatchSyncStatus.COMPLETED, job.error_message
    svc._fetch_template_version.assert_awaited_once_with("TPL-CHILD", 3)
    ensured = svc.schema_manager.ensure_table_for_template.await_args.args[1]
    assert [f["name"] for f in ensured["fields"]] == ["inherited", "own"]


@pytest.mark.asyncio
async def test_a_template_that_cannot_be_read_resolved_fails_the_job(svc):
    # Template Store refuses a broken chain (409); a table built from the
    # child's own fields would be the partial schema the refusal prevents.
    svc._fetch_template_version = AsyncMock(return_value=None)
    job = _job()

    await svc._run_batch_sync(job, dict(STORED), force=False, page_size=100)

    assert job.status == BatchSyncStatus.FAILED
    assert "could not be read resolved" in job.error_message
    svc.schema_manager.ensure_table_for_template.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_template_without_a_parent_is_used_as_listed(svc):
    svc._fetch_template_version = AsyncMock()
    plain = {k: v for k, v in STORED.items() if k not in ("extends", "extends_version")}

    await svc._run_batch_sync(_job(), plain, force=False, page_size=100)

    svc._fetch_template_version.assert_not_awaited()


def _http(*responses: MagicMock) -> MagicMock:
    http = MagicMock()
    http.get = AsyncMock(side_effect=list(responses))
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=None)
    return http


def _response(body: dict) -> MagicMock:
    response = MagicMock(status_code=200)
    response.json = MagicMock(return_value=body)
    response.raise_for_status = MagicMock()
    return response


@pytest.mark.asyncio
async def test_the_latest_active_version_is_reread_resolved(svc):
    svc._fetch_template_version = AsyncMock(return_value=RESOLVED)
    http = _http(_response({"items": [STORED]}))
    with patch("reporting_sync.batch_sync.httpx.AsyncClient", return_value=http):
        template = await svc._fetch_latest_active_version("CHILD", "kb")

    assert template == RESOLVED
    svc._fetch_template_version.assert_awaited_once_with("TPL-CHILD", 3)


@pytest.mark.asyncio
async def test_parity_compares_against_the_resolved_template():
    http = _http(_response({"items": [STORED]}), _response(RESOLVED))
    with patch("reporting_sync.parity.httpx.AsyncClient", return_value=http):
        templates = await parity._fetch_namespace_templates("kb")

    assert templates == [RESOLVED]
    url = http.get.await_args_list[1].args[0]
    assert url.endswith("/api/template-store/templates/TPL-CHILD")
    assert http.get.await_args_list[1].kwargs["params"] == {"version": "3"}
