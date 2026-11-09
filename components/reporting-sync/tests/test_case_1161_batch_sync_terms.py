"""The batch term sync, pinned before batch_sync_terms' refactor.

Provenance: CASE-1161. The suite never reached these branches: a second
page of the terminology listing; a failed terminology or term listing (the
result is then not complete); an empty term page; a second term page; a term
whose names are gone (its stale name rows are cleared, none written); a term that fails
to sync (counted, the sync goes on); the progress log every ten
terminologies; an error outside the per-term handling; and no closure
rebuild when nothing synced.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reporting_sync.batch_sync import BatchSyncService


def _resp(items, pages=1, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"items": items, "page": 1, "pages": pages}
    return r


def _term(term_id: str, value: str | None, aliases: list[str]) -> dict:
    t = {"term_id": term_id, "namespace": "wip", "terminology_id": "T0",
         "terminology_value": "X", "aliases": aliases}
    if value is not None:
        t["value"] = value
    return t


class FakeDefStore:
    def __init__(self, list_status=200):
        self.list_status = list_status

    async def get(self, url, params=None, headers=None):
        page = params["page"]
        if url.endswith("/terminologies"):
            if self.list_status != 200:
                return _resp([], status=self.list_status)
            ids = [f"T{i}" for i in range(10)] if page == 1 else ["T10", "T11"]
            return _resp([{"terminology_id": i} for i in ids], pages=2)
        tid = url.split("/terminologies/")[1].split("/")[0]
        if tid == "T0":
            if page == 1:
                return _resp([_term("a", "A", ["alpha"])], pages=2)
            return _resp([_term("b", "B", [])], pages=2)
        if tid == "T2":
            return _resp([], status=500)
        if tid == "T3":
            return _resp([_term("c", None, [])])
        return _resp([])


def _client(store):
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=store.get)
    return client


@pytest.fixture
def svc():
    pool = MagicMock()
    conn = AsyncMock()

    async def fetch(sql, ns, term_ids):
        # The names table holds a stale name for term b, which has none now.
        stale = {"term_id": "b", "terminology_id": "T0", "value": "old-b",
                 "type": None, "language": None, "source": None, "preferred": False}
        return [stale] if "b" in term_ids else []

    conn.fetch = AsyncMock(side_effect=fetch)
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm
    service = BatchSyncService(pool)
    service._conn = conn
    service.schema_manager.ensure_terms_table = AsyncMock(return_value="terms")
    service.schema_manager.ensure_term_names_table = AsyncMock(return_value="term_names")
    service.closure.rebuild = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_pages_failures_and_progress(svc, caplog):
    with patch("reporting_sync.batch_sync.httpx.AsyncClient", return_value=_client(FakeDefStore())), \
         patch("reporting_sync.batch_sync.asyncio.sleep", AsyncMock()), caplog.at_level("INFO"):
        result = await svc.batch_sync_terms()

    assert result == {"synced": 2, "failed": 1, "total": 3, "complete": False}
    deletes = [c.args[1:] for c in svc._conn.execute.await_args_list if c.args[0].startswith("DELETE")]
    assert deletes == [("wip", "a"), ("wip", "b")]
    written = [rows for c in svc._conn.executemany.await_args_list for rows in [c.args[1]]]
    assert [[r[3] for r in rows] for rows in written] == [["alpha"]]
    assert "Found 12 terminologies" in caplog.text
    assert "Term sync progress: 10/12 terminologies, 2 terms synced" in caplog.text
    svc.closure.rebuild.assert_awaited_once_with("wip")


@pytest.mark.asyncio
async def test_a_failed_terminology_listing_is_not_complete_and_rebuilds_nothing(svc):
    with patch("reporting_sync.batch_sync.httpx.AsyncClient", return_value=_client(FakeDefStore(503))):
        result = await svc.batch_sync_terms()
    assert result == {"synced": 0, "failed": 0, "total": 0, "complete": False}
    svc.closure.rebuild.assert_not_called()


@pytest.mark.asyncio
async def test_an_error_outside_the_per_term_handling_ends_the_sync_incomplete(svc):
    svc.schema_manager.ensure_terms_table = AsyncMock(side_effect=RuntimeError("pg gone"))
    with patch("reporting_sync.batch_sync.httpx.AsyncClient", return_value=_client(FakeDefStore())), \
         patch("reporting_sync.batch_sync.asyncio.sleep", AsyncMock()):
        result = await svc.batch_sync_terms()
    assert result == {"synced": 0, "failed": 0, "total": 0, "complete": False}
    svc.closure.rebuild.assert_not_called()
