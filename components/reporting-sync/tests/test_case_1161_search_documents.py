"""Document search over the reporting tables, pinned before
_search_documents' refactor.

Provenance: CASE-1161. The suite never reached these branches: a namespace
filter narrows table discovery to that schema; a table with no columns is
skipped; a table whose query fails is skipped and the search goes on; the
per-type cap stops the table walk; and a failure outside the per-table
handling falls back to the REST scan.
"""

from unittest.mock import AsyncMock

import pytest

from reporting_sync import search_service
from reporting_sync.search_service import SearchResult, SearchService

from .test_search_endpoint import _mock_pool_with_conn, _RecordingConn

TEXT = [("document_id", "text"), ("status", "text"), ("title", "text")]
FTS = [*TEXT, ("title_search", "text"), ("title_tsv", "tsvector")]


class _FailingConn(_RecordingConn):
    """Fails every content query against one table."""

    def __init__(self, failing: str):
        super().__init__()
        self.failing = failing

    async def _dispatch(self, sql, *params, timeout=None):
        if "information_schema" not in sql and f'"{self.failing}"' in sql:
            self.executed_sql.append((sql, params))
            raise RuntimeError("relation vanished")
        return await super()._dispatch(sql, *params)


@pytest.mark.asyncio
async def test_namespace_filter_empty_and_failing_tables():
    conn = _FailingConn("doc_b__v1")
    conn.tables = ["doc_a__v1", "doc_b__v1", "doc_c__v1"]
    conn.columns = {"doc_b__v1": FTS, "doc_c__v1": TEXT}
    conn.rows_per_query = [[{"doc_id": "d-c", "status": "active", "updated_at": None}]]
    results = await SearchService(_mock_pool_with_conn(conn))._search_documents("hello", "ns1", None)

    discovery_sql, discovery_params = conn.executed_sql[0]
    assert "table_schema = $1" in discovery_sql and discovery_params == ("ns1",)
    assert [(r.id, r.value, r.description, r.score) for r in results] == [
        ("d-c", None, "Matched in doc_c__v1", None),  # no template_id: value unknown
    ]
    queried = [s for s, _ in conn.executed_sql if "information_schema" not in s]
    assert len(queried) == 2  # doc_b's FTS query (failed) and doc_c's substring query
    assert "plainto_tsquery" in queried[0] and "ILIKE" in queried[1]


@pytest.mark.asyncio
async def test_the_cap_stops_the_table_walk(monkeypatch):
    monkeypatch.setattr(search_service, "MAX_RESULTS_PER_TYPE", 2)
    conn = _RecordingConn()
    conn.tables = ["doc_a__v1", "doc_b__v1"]
    conn.columns = {"doc_a__v1": TEXT, "doc_b__v1": TEXT}
    conn.rows_per_query = [[
        {"doc_id": "d1", "status": "active", "updated_at": None},
        {"doc_id": "d2", "status": "inactive", "updated_at": None},
    ]]
    results = await SearchService(_mock_pool_with_conn(conn))._search_documents("x", None, None)
    assert [(r.id, r.status) for r in results] == [("d1", "active"), ("d2", "inactive")]
    columns_read = [p[1] for s, p in conn.executed_sql if "information_schema.columns" in s]
    assert columns_read == ["doc_a__v1"]
    limit_param = next(p for s, p in conn.executed_sql if "ILIKE" in s)[-1]
    assert limit_param == 2


@pytest.mark.asyncio
async def test_a_failure_outside_the_tables_falls_back_to_the_rest_scan():
    conn = _RecordingConn()
    conn.fetch = AsyncMock(side_effect=RuntimeError("pg down"))
    svc = SearchService(_mock_pool_with_conn(conn))
    hit = SearchResult(type="document", id="rest-1", value=None, label="x document")
    svc._search_documents_rest_fallback = AsyncMock(return_value=[hit])
    assert await svc._search_documents("q", "ns1", "active") == [hit]
    svc._search_documents_rest_fallback.assert_awaited_once_with("q", "ns1", "active", 1000)
