"""The batch-sync trigger's answer names each job's template by id.

The template list is instance-wide and two namespaces can share a template
value, so a caller matching the answer's jobs to its own templates (a
restore explaining why a reporting table never appeared) needs the id. The
job record always carried it; the answer's model dropped it, so such a
caller could match nothing.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync import main
from reporting_sync.models import BatchSyncJob, BatchSyncStatus


@pytest.mark.asyncio
async def test_the_trigger_answer_carries_each_jobs_template_id(monkeypatch):
    service = MagicMock()
    service.start_batch_sync_all = AsyncMock(return_value=[
        BatchSyncJob(job_id="j1", template_value="SESSION", template_id="TPL-1",
                     namespace="kb", status=BatchSyncStatus.PENDING),
        BatchSyncJob(job_id="j2", template_value="SESSION", template_id="TPL-2",
                     namespace="kb", status=BatchSyncStatus.FAILED,
                     error_message="source unavailable"),
    ])
    monkeypatch.setattr(main.state, "batch_sync_service", service)
    transport = ASGITransport(app=main.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"X-API-Key": "rs_test_admin_key"}) as c:
        r = await c.post("/api/reporting-sync/sync/batch", params={"namespace": "kb"})
    assert r.status_code == 200
    assert [(j["job_id"], j["template_id"], j["status"]) for j in r.json()] == [
        ("j1", "TPL-1", "pending"), ("j2", "TPL-2", "failed"),
    ]
