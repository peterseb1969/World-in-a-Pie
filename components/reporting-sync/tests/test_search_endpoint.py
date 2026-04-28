"""Tests for Phase 3 of the full-text-search feature.

Covers the /api/reporting-sync/search endpoint behaviour:
- SearchRequest schema accepts mode/include_inactive/snippet_format/template
- SearchResult schema carries score + snippet
- Dispatch: per-table choice between FTS and substring based on
  tsv-column availability and the requested mode
- SQL shape: FTS query uses an OR-of-tsvectors WHERE clause (so each
  GIN index can be used independently), ts_rank concatenation for
  scoring, ts_headline for snippets
- include_inactive=False (default) adds WHERE status='active'
- snippet_format=text vs html toggles ts_headline StartSel/StopSel

The asyncpg connection is mocked. Real-DB E2E coverage is left for the
integration suite (which needs a live Postgres + reporting-sync stack).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from reporting_sync.search_service import (
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchService,
)


# =========================================================================
# SearchRequest schema
# =========================================================================


def test_search_request_defaults():
    req = SearchRequest(query="lesson")
    assert req.query == "lesson"
    assert req.mode == "auto"
    assert req.include_inactive is False
    assert req.snippet_format == "html"
    assert req.template is None
    assert req.limit == 50


def test_search_request_accepts_explicit_options():
    req = SearchRequest(
        query="lesson",
        mode="fts",
        include_inactive=True,
        snippet_format="text",
        template="LESSON",
        namespace="kb",
        limit=10,
    )
    assert req.mode == "fts"
    assert req.include_inactive is True
    assert req.snippet_format == "text"
    assert req.template == "LESSON"


def test_search_request_rejects_invalid_mode():
    with pytest.raises(ValidationError):
        SearchRequest(query="x", mode="invalid")


def test_search_request_rejects_invalid_snippet_format():
    with pytest.raises(ValidationError):
        SearchRequest(query="x", snippet_format="rtf")


def test_search_request_rejects_unknown_field():
    with pytest.raises(ValidationError):
        SearchRequest(query="x", weird_extra=True)


# =========================================================================
# SearchResult schema
# =========================================================================


def test_search_result_carries_score_and_snippet():
    r = SearchResult(
        type="document",
        id="DOC-1",
        score=0.42,
        snippet="A <b>lesson</b> excerpt.",
    )
    assert r.score == 0.42
    assert r.snippet == "A <b>lesson</b> excerpt."


def test_search_result_score_and_snippet_optional():
    r = SearchResult(type="document", id="DOC-1")
    assert r.score is None
    assert r.snippet is None


# =========================================================================
# Helpers — mock conn that records executed SQL
# =========================================================================


class _RecordingConn:
    """Mocked asyncpg connection that records SQL and returns canned data."""

    def __init__(self):
        self.fetch = AsyncMock(side_effect=self._dispatch)
        self.executed_sql: list[tuple[str, tuple]] = []
        # state controlled by the test
        self.tables: list[str] = []
        self.columns: dict[str, list[tuple[str, str]]] = {}  # table -> [(name, type)]
        self.rows_per_query: list[list[dict]] = []

    async def _dispatch(self, sql: str, *params):
        self.executed_sql.append((sql, params))
        # Table discovery query (info_schema.tables)
        if "information_schema.tables" in sql:
            return [{"table_name": t} for t in self.tables]
        # Column discovery query
        if "information_schema.columns" in sql:
            tname = params[0]
            return [
                {"column_name": n, "data_type": t}
                for n, t in self.columns.get(tname, [])
            ]
        # Otherwise return next prepared row set.
        if self.rows_per_query:
            return self.rows_per_query.pop(0)
        return []


def _mock_pool_with_conn(conn: _RecordingConn):
    pool = MagicMock()
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm
    return pool


# =========================================================================
# Dispatch — auto/fts/substring picking
# =========================================================================


@pytest.mark.asyncio
async def test_auto_mode_uses_fts_when_tsv_columns_present():
    conn = _RecordingConn()
    conn.tables = ["doc_lesson"]
    conn.columns["doc_lesson"] = [
        ("document_id", "text"),
        ("namespace", "character varying"),
        ("status", "character varying"),
        ("body", "text"),
        ("body_search", "text"),
        ("body_tsv", "tsvector"),
    ]
    conn.rows_per_query = [
        [{"doc_id": "DOC-1", "status": "active", "updated_at": None,
          "score": 0.5, "snippet": "hit"}]
    ]
    svc = SearchService(_mock_pool_with_conn(conn))
    results = await svc._search_documents("term", None, None, 10, mode="auto")
    # An FTS query was issued — look for plainto_tsquery + ts_rank.
    fts_calls = [s for s, _ in conn.executed_sql if "plainto_tsquery" in s]
    assert fts_calls, "expected an FTS query to run"
    assert any("ts_rank" in s for s in fts_calls)
    assert results
    assert results[0].score == 0.5
    assert results[0].snippet == "hit"


@pytest.mark.asyncio
async def test_auto_mode_falls_back_to_substring_when_no_tsv_columns():
    conn = _RecordingConn()
    conn.tables = ["doc_legacy"]
    conn.columns["doc_legacy"] = [
        ("document_id", "text"),
        ("namespace", "character varying"),
        ("status", "character varying"),
        ("title", "text"),
    ]
    conn.rows_per_query = [
        [{"doc_id": "DOC-1", "status": "active", "updated_at": None}]
    ]
    svc = SearchService(_mock_pool_with_conn(conn))
    results = await svc._search_documents("term", None, None, 10, mode="auto")
    # Substring path = ILIKE, no plainto_tsquery.
    assert not any("plainto_tsquery" in s for s, _ in conn.executed_sql)
    assert any("ILIKE" in s for s, _ in conn.executed_sql)
    assert results
    assert results[0].score is None  # substring matches don't carry scores


@pytest.mark.asyncio
async def test_fts_mode_skips_table_without_tsv_columns():
    conn = _RecordingConn()
    conn.tables = ["doc_no_fts"]
    conn.columns["doc_no_fts"] = [
        ("document_id", "text"),
        ("status", "character varying"),
        ("title", "text"),
    ]
    svc = SearchService(_mock_pool_with_conn(conn))
    results = await svc._search_documents("term", None, None, 10, mode="fts")
    # No data query should have been issued — only metadata discovery.
    data_queries = [
        s for s, _ in conn.executed_sql
        if "information_schema" not in s
    ]
    assert not data_queries
    assert results == []


@pytest.mark.asyncio
async def test_substring_mode_forces_ilike_even_with_tsv_columns():
    conn = _RecordingConn()
    conn.tables = ["doc_lesson"]
    conn.columns["doc_lesson"] = [
        ("document_id", "text"),
        ("namespace", "character varying"),
        ("status", "character varying"),
        ("body", "text"),
        ("body_search", "text"),
        ("body_tsv", "tsvector"),
    ]
    conn.rows_per_query = [
        [{"doc_id": "DOC-1", "status": "active", "updated_at": None}]
    ]
    svc = SearchService(_mock_pool_with_conn(conn))
    await svc._search_documents("term", None, None, 10, mode="substring")
    assert any("ILIKE" in s for s, _ in conn.executed_sql)
    assert not any("plainto_tsquery" in s for s, _ in conn.executed_sql)


# =========================================================================
# template= filter
# =========================================================================


@pytest.mark.asyncio
async def test_template_filter_restricts_table_lookup():
    conn = _RecordingConn()
    conn.tables = ["doc_lesson"]  # whatever the lookup returns
    conn.columns["doc_lesson"] = [
        ("document_id", "text"),
        ("status", "character varying"),
        ("body", "text"),
        ("body_search", "text"),
        ("body_tsv", "tsvector"),
    ]
    conn.rows_per_query = [
        [{"doc_id": "DOC-1", "status": "active", "updated_at": None,
          "score": 0.3, "snippet": "x"}]
    ]
    svc = SearchService(_mock_pool_with_conn(conn))
    await svc._search_documents("term", None, None, 10, template="LESSON")
    table_lookups = [
        (s, p) for s, p in conn.executed_sql
        if "information_schema.tables" in s
    ]
    assert table_lookups
    # The discovery query should include the doc_lesson literal as a
    # parameter (template lower-cased + 'doc_' prefix).
    sql, params = table_lookups[0]
    assert "table_name = $1" in sql
    assert params == ("doc_lesson",)


# =========================================================================
# include_inactive flag affects WHERE clause
# =========================================================================


@pytest.mark.asyncio
async def test_default_excludes_inactive_documents():
    conn = _RecordingConn()
    conn.tables = ["doc_lesson"]
    conn.columns["doc_lesson"] = [
        ("document_id", "text"),
        ("status", "character varying"),
        ("body", "text"),
        ("body_search", "text"),
        ("body_tsv", "tsvector"),
    ]
    conn.rows_per_query = [[]]
    svc = SearchService(_mock_pool_with_conn(conn))
    await svc._search_documents("term", None, None, 10)
    fts_sql = next(s for s, _ in conn.executed_sql if "plainto_tsquery" in s)
    assert "status = 'active'" in fts_sql


@pytest.mark.asyncio
async def test_include_inactive_drops_status_filter():
    conn = _RecordingConn()
    conn.tables = ["doc_lesson"]
    conn.columns["doc_lesson"] = [
        ("document_id", "text"),
        ("status", "character varying"),
        ("body", "text"),
        ("body_search", "text"),
        ("body_tsv", "tsvector"),
    ]
    conn.rows_per_query = [[]]
    svc = SearchService(_mock_pool_with_conn(conn))
    await svc._search_documents("term", None, None, 10, include_inactive=True)
    fts_sql = next(s for s, _ in conn.executed_sql if "plainto_tsquery" in s)
    assert "status = 'active'" not in fts_sql


@pytest.mark.asyncio
async def test_include_inactive_substring_drops_status_filter():
    conn = _RecordingConn()
    conn.tables = ["doc_legacy"]
    conn.columns["doc_legacy"] = [
        ("document_id", "text"),
        ("status", "character varying"),
        ("title", "text"),
    ]
    conn.rows_per_query = [[]]
    svc = SearchService(_mock_pool_with_conn(conn))
    await svc._search_documents(
        "term", None, None, 10, mode="substring", include_inactive=True
    )
    sub_sql = next(s for s, _ in conn.executed_sql if "ILIKE" in s)
    assert "status = 'active'" not in sub_sql


# =========================================================================
# Snippet format
# =========================================================================


@pytest.mark.asyncio
async def test_snippet_format_html_uses_b_tags():
    conn = _RecordingConn()
    conn.tables = ["doc_lesson"]
    conn.columns["doc_lesson"] = [
        ("document_id", "text"),
        ("status", "character varying"),
        ("body", "text"),
        ("body_search", "text"),
        ("body_tsv", "tsvector"),
    ]
    conn.rows_per_query = [[]]
    svc = SearchService(_mock_pool_with_conn(conn))
    await svc._search_documents("term", None, None, 10, snippet_format="html")
    fts_sql = next(s for s, _ in conn.executed_sql if "plainto_tsquery" in s)
    assert "StartSel=<b>" in fts_sql
    assert "StopSel=</b>" in fts_sql


@pytest.mark.asyncio
async def test_snippet_format_text_omits_b_tags():
    conn = _RecordingConn()
    conn.tables = ["doc_lesson"]
    conn.columns["doc_lesson"] = [
        ("document_id", "text"),
        ("status", "character varying"),
        ("body", "text"),
        ("body_search", "text"),
        ("body_tsv", "tsvector"),
    ]
    conn.rows_per_query = [[]]
    svc = SearchService(_mock_pool_with_conn(conn))
    await svc._search_documents("term", None, None, 10, snippet_format="text")
    fts_sql = next(s for s, _ in conn.executed_sql if "plainto_tsquery" in s)
    assert "StartSel=<b>" not in fts_sql
    assert "StopSel=" not in fts_sql or "StartSel=" not in fts_sql


# =========================================================================
# Multi-tsv column FTS — OR-of-tsvectors uses each GIN index
# =========================================================================


@pytest.mark.asyncio
async def test_multi_field_fts_uses_or_of_tsvectors():
    conn = _RecordingConn()
    conn.tables = ["doc_lesson"]
    conn.columns["doc_lesson"] = [
        ("document_id", "text"),
        ("status", "character varying"),
        ("title", "text"),
        ("title_search", "text"),
        ("title_tsv", "tsvector"),
        ("body", "text"),
        ("body_search", "text"),
        ("body_tsv", "tsvector"),
    ]
    conn.rows_per_query = [[]]
    svc = SearchService(_mock_pool_with_conn(conn))
    await svc._search_documents("term", None, None, 10)
    fts_sql = next(s for s, _ in conn.executed_sql if "plainto_tsquery" in s)
    # OR clause means each GIN index can be used.
    assert '"title_tsv" @@ q.query' in fts_sql
    assert '"body_tsv" @@ q.query' in fts_sql
    assert " OR " in fts_sql
    # ts_rank concatenates for scoring.
    assert '"title_tsv" || "body_tsv"' in fts_sql


# =========================================================================
# Cross-template ordering — FTS hits sort before substring hits
# =========================================================================


@pytest.mark.asyncio
async def test_fts_results_sorted_before_substring_results_in_auto_mode():
    conn = _RecordingConn()
    conn.tables = ["doc_indexed", "doc_legacy"]
    conn.columns["doc_indexed"] = [
        ("document_id", "text"),
        ("status", "character varying"),
        ("body", "text"),
        ("body_search", "text"),
        ("body_tsv", "tsvector"),
    ]
    conn.columns["doc_legacy"] = [
        ("document_id", "text"),
        ("status", "character varying"),
        ("title", "text"),
    ]
    # First the FTS row, then the substring row (in execution order).
    conn.rows_per_query = [
        [{"doc_id": "DOC-INDEXED", "status": "active", "updated_at": None,
          "score": 0.4, "snippet": "x"}],
        [{"doc_id": "DOC-LEGACY", "status": "active", "updated_at": None}],
    ]
    svc = SearchService(_mock_pool_with_conn(conn))
    results = await svc._search_documents("term", None, None, 10, mode="auto")
    assert len(results) == 2
    # FTS hit (with score) comes first regardless of execution order.
    assert results[0].id == "DOC-INDEXED"
    assert results[0].score == 0.4
    assert results[1].id == "DOC-LEGACY"
    assert results[1].score is None


# =========================================================================
# Empty-pool fallback
# =========================================================================


@pytest.mark.asyncio
async def test_no_postgres_pool_falls_back_to_rest():
    svc = SearchService(postgres_pool=None)
    # Attribute the rest fallback function so we can confirm the dispatch.
    called = {"n": 0}

    async def fake_rest_fallback(*_args, **_kwargs):
        called["n"] += 1
        return []

    svc._search_documents_rest_fallback = fake_rest_fallback  # type: ignore[method-assign]
    await svc._search_documents("term", None, None, 10)
    assert called["n"] == 1
