"""The batch sync pages documents by cursor, not by offset.

Provenance: CASE-967 — a namespace's catch-up after a restore took ~4 minutes
for 115k documents. Offset paging made the document-store sort every matching
document on every page (the default order cannot use an index): measured on
43,856 documents, page 1 and page 40 both examined all 43,856. A cursor page
reads one page of an index instead. The total now comes from a single
one-document request.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reporting_sync.batch_sync import FIRST_CURSOR, BatchSyncService
from reporting_sync.models import BatchSyncJob, BatchSyncStatus

TEMPLATE = {
    "template_id": "TPL-1",
    "namespace": "ct",
    "value": "CT_TRIAL_AE",
    "version": 1,
    "fields": [{"name": "code", "type": "string", "label": "Code", "mandatory": False}],
    "reporting": {"sync_enabled": True, "sync_strategy": "latest_only"},
}


def _doc(i: int) -> dict:
    return {
        "document_id": f"d{i:05d}",
        "namespace": "ct",
        "template_id": "TPL-1",
        "template_version": 1,
        "version": 1,
        "status": "active",
        "identity_hash": f"h{i}",
        "data": {"code": str(i)},
        "created_at": "2026-10-29T10:00:00Z",
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
    service.schema_manager.ensure_table_for_template = AsyncMock(return_value="doc_ct_trial_ae__v1")
    service.schema_manager.ensure_views_for_template = AsyncMock(return_value=None)
    service.schema_manager.list_version_tables = AsyncMock(return_value={1: "doc_ct_trial_ae__v1"})
    return service


class FakeDocumentStore:
    """Serves a corpus the way the list endpoint does: offset mode with a
    total, cursor mode with a next cursor while pages are full."""

    def __init__(self, n: int):
        self.corpus = [_doc(i) for i in range(n)]
        self.calls: list[dict] = []

    async def fetch(self, template_id, page, page_size, status="active",
                    namespace=None, cursor=None):
        self.calls.append({"page": page, "page_size": page_size, "cursor": cursor})
        if cursor is None:
            start = (page - 1) * page_size
            return self.corpus[start:start + page_size], len(self.corpus), None
        start = 0 if cursor == FIRST_CURSOR else int(cursor)
        items = self.corpus[start:start + page_size]
        next_cursor = str(start + page_size) if len(items) == page_size else None
        return items, -1, next_cursor


def _job() -> BatchSyncJob:
    return BatchSyncJob(job_id="j1", template_value="CT_TRIAL_AE", template_id="TPL-1",
                        namespace="ct", status=BatchSyncStatus.PENDING)


@pytest.mark.asyncio
@pytest.mark.parametrize("n,pages", [(2500, 3), (2000, 3), (999, 1)],
                         ids=["partial-last-page", "exact-multiple", "single-page"])
async def test_every_document_is_synced_once_by_cursor(svc, n, pages):
    store = FakeDocumentStore(n)
    svc._fetch_documents = AsyncMock(side_effect=store.fetch)

    job = _job()
    await svc._run_batch_sync(job, TEMPLATE, force=False, page_size=1000)

    assert job.status == BatchSyncStatus.COMPLETED
    assert job.total_documents == n
    assert job.documents_synced == n
    assert job.rows_written == n

    probe, *page_calls = store.calls
    # The total: one offset request for a single document.
    assert probe == {"page": 1, "page_size": 1, "cursor": None}
    # Every page after it is a cursor page, starting below the first _id.
    assert page_calls[0]["cursor"] == FIRST_CURSOR
    assert all(c["cursor"] is not None and c["page_size"] == 1000 for c in page_calls)
    # An exact multiple costs one extra, empty cursor page — nothing more.
    assert len(page_calls) == pages


@pytest.mark.asyncio
async def test_empty_template_stops_after_the_probe(svc):
    store = FakeDocumentStore(0)
    svc._fetch_documents = AsyncMock(side_effect=store.fetch)

    job = _job()
    await svc._run_batch_sync(job, TEMPLATE, force=False, page_size=1000)

    assert job.status == BatchSyncStatus.COMPLETED
    assert len(store.calls) == 1


@pytest.mark.asyncio
async def test_fetch_documents_sends_the_cursor_and_returns_the_next(svc):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"items": [{"document_id": "a"}], "total": -1,
                              "next_cursor": "65f0c0ffee0000000000000a"}
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=resp)

    with patch("reporting_sync.batch_sync.httpx.AsyncClient", return_value=client):
        items, total, next_cursor = await svc._fetch_documents(
            "TPL-1", 1, 1000, namespace="ct", cursor=FIRST_CURSOR,
        )

    params = client.get.call_args.kwargs["params"]
    assert params["cursor"] == FIRST_CURSOR
    assert (items, total, next_cursor) == (
        [{"document_id": "a"}], -1, "65f0c0ffee0000000000000a",
    )


@pytest.mark.asyncio
async def test_fetch_documents_without_cursor_sends_none(svc):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"items": [], "total": 7}
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=resp)

    with patch("reporting_sync.batch_sync.httpx.AsyncClient", return_value=client):
        _items, total, next_cursor = await svc._fetch_documents("TPL-1", 1, 1)

    assert "cursor" not in client.get.call_args.kwargs["params"]
    assert (total, next_cursor) == (7, None)
