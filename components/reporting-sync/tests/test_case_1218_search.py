"""Document search against real reporting rows.

The search beds used to mock a connection, so they pinned SQL text, not
what PostgreSQL does with it. These write rows the way the sync does
(SchemaManager tables, DocumentTransformer rows) and search them:

- an explicit status returns documents in that status only;
- a template without metadata columns (include_metadata: false) is still
  found by full-text search;
- substring search reads document content, not system columns: "active"
  does not match every active row;
- a document kept in several rows (all_versions) is one hit;
- HTML snippets escape the document's text and bold only the matches.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from pydantic import ValidationError

from reporting_sync import search_service
from reporting_sync.main import init_postgres_schema
from reporting_sync.models import ReportingConfig
from reporting_sync.schema_manager import SchemaManager
from reporting_sync.search_service import SearchRequest, SearchService
from reporting_sync.transformer import DocumentTransformer

from .conftest import requires_postgres

NS = "rs1218s"

NOTE = {
    "template_id": "tpl-note", "value": "Note", "version": 1,
    "fields": [{"name": "title", "type": "string", "full_text_indexed": True}],
    "identity_fields": ["title"],
    "reporting": {"sync_enabled": True, "include_metadata": False},
}
MEMO = {
    "template_id": "tpl-memo", "value": "Memo", "version": 1,
    "fields": [{"name": "body", "type": "string"}],
    "identity_fields": ["body"],
    "reporting": {"sync_enabled": True, "sync_strategy": "all_versions"},
}


def _doc(template, doc_id, data, status="active", version=1):
    return {
        "document_id": doc_id, "namespace": NS, "template_id": template["template_id"],
        "template_version": 1, "version": version, "status": status,
        "identity_hash": doc_id, "created_at": "2026-11-09T10:00:00Z", "data": data,
    }


async def _write(pool, template, docs):
    table = await SchemaManager(pool).ensure_table_for_template(NS, template)
    reporting = template["reporting"]
    transformer = DocumentTransformer(ReportingConfig(**reporting))
    async with pool.acquire() as conn:
        for doc in docs:
            for row in transformer.transform(doc, template):
                sql, params = transformer.generate_upsert_sql(
                    table, row, reporting.get("sync_strategy", "latest_only"),
                )
                await conn.execute(sql, *params)


@pytest_asyncio.fixture
async def svc(pg_pool, monkeypatch):
    await init_postgres_schema(pg_pool)
    await _write(pg_pool, NOTE, [
        _doc(NOTE, "n-1", {"title": "chicken soup"}),
        _doc(NOTE, "n-2", {"title": "chicken pie"}, status="inactive"),
        _doc(NOTE, "n-3", {"title": "chicken & rice <img src=x onerror=alert(1)> rice"}),
    ])
    await _write(pg_pool, MEMO, [
        _doc(MEMO, "m-1", {"body": "buy chicken"}, version=1),
        _doc(MEMO, "m-1", {"body": "buy more chicken"}, version=2),
        _doc(MEMO, "m-2", {"body": "nothing to see"}),
    ])

    async def values(client, ids):
        return {"tpl-note": "Note", "tpl-memo": "Memo"}

    monkeypatch.setattr(search_service, "_template_values", values)
    return SearchService(pg_pool)


@requires_postgres
@pytest.mark.asyncio
async def test_status_filter_returns_that_status_only(svc):
    hits = await svc._search_documents("chicken", NS, "inactive", template="Note")
    assert [h.id for h in hits] == ["n-2"]
    default = await svc._search_documents("chicken", NS, None, template="Note")
    assert sorted(h.id for h in default) == ["n-1", "n-3"]


@requires_postgres
@pytest.mark.asyncio
async def test_fts_finds_a_template_without_metadata_columns(svc):
    hits = await svc._search_documents("soup", NS, None, mode="fts")
    assert [(h.id, h.value, h.updated_at) for h in hits] == [("n-1", "Note", None)]
    assert hits[0].score is not None


@requires_postgres
@pytest.mark.asyncio
async def test_substring_search_skips_system_columns(svc):
    assert await svc._search_documents("active", NS, None, mode="substring") == []
    assert await svc._search_documents("tpl-memo", NS, None, mode="substring") == []
    hits = await svc._search_documents("nothing", NS, None, mode="substring")
    assert [h.id for h in hits] == ["m-2"]


@requires_postgres
@pytest.mark.asyncio
async def test_a_document_with_several_rows_is_one_hit(svc):
    hits = await svc._search_documents("chicken", NS, None, template="Memo", mode="substring")
    assert [(h.id, h.value) for h in hits] == [("m-1", "Memo")]


@requires_postgres
@pytest.mark.asyncio
async def test_html_snippets_escape_the_document_text(svc):
    hits = await svc._search_documents("rice", NS, None, mode="fts")
    assert [h.id for h in hits] == ["n-3"]
    snippet = hits[0].snippet
    # The only markup left is the platform's own <b>…</b>.
    assert snippet.replace("<b>", "").replace("</b>", "").count("<") == 0
    assert "&amp;" in snippet and "<b>rice</b>" in snippet
    text = await svc._search_documents("rice", NS, None, mode="fts", snippet_format="text")
    assert "<b>" not in text[0].snippet and "&" in text[0].snippet


# ---------------------------------------------------------------------------
# Request validation (no PostgreSQL)
# ---------------------------------------------------------------------------


def test_unknown_types_are_refused():
    with pytest.raises(ValidationError):
        SearchRequest(query="x", types=["documents"])
    assert SearchRequest(query="x", types=["document", "term"]).types == ["document", "term"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,expected",
    [
        ({"limit": 7}, 7),  # the alias stands in for an absent page_size
        ({"limit": 7, "page_size": 50}, 50),  # an explicit page_size wins, even 50
        ({"limit": 7, "page_size": 20}, 20),
        ({}, 50),
    ],
)
async def test_limit_is_used_only_without_page_size(body, expected):
    svc = SearchService(None)

    async def nothing(*a, **k):
        return []

    svc._search_terms = nothing
    result = await svc.search(SearchRequest(query="x", types=["term"], **body))
    assert result.results["term"].page_size == expected


# ---------------------------------------------------------------------------
# A type that could not search everywhere says so (no PostgreSQL)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_type_is_an_error_not_zero_hits():
    svc = SearchService(None)

    async def down(*a, **k):
        raise search_service.UpstreamError("def-store answered HTTP 503")

    async def some(*a, **k):
        return search_service._hits(
            [search_service.SearchResult(type="template", id="t1", value="T")],
            "matched against the first 1000 entries only",
        )

    svc._search_terms = down
    svc._search_templates = some
    result = await svc.search(SearchRequest(query="x", types=["term", "template"]))
    term, template = result.results["term"], result.results["template"]
    assert (term.complete, term.total) == (False, 0)
    assert "503" in term.error
    assert (template.complete, template.total) == (False, 1)
    assert template.error == "matched against the first 1000 entries only"


@requires_postgres
@pytest.mark.asyncio
async def test_a_table_that_could_not_be_searched_marks_the_documents_incomplete(svc, monkeypatch):
    real = SearchService._fts_query

    async def failing(self, conn, schema, tname, *a, **k):
        if tname.startswith("doc_note"):
            raise RuntimeError("relation vanished")
        return await real(self, conn, schema, tname, *a, **k)

    monkeypatch.setattr(SearchService, "_fts_query", failing)
    hits = await svc._search_documents("chicken", NS, None)
    assert [h.id for h in hits] == ["m-1"]
    assert "doc_note__v1" in hits.incomplete
