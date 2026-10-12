"""Listings are scoped inside reporting-sync (CASE-851, Phase 2b C5).

reporting-sync reads the stores with its own service key, so the stores
cannot scope a listing to the caller — reporting-sync must pass the
caller's effective namespace down itself. These tests check that the
namespace actually reaches every upstream call and SQL scan, not just the
route.
"""

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from reporting_sync.search_service import SearchService, _with_namespace

DEF = "http://localhost:8002"
TPL = "http://localhost:8003"
DOC = "http://localhost:8004"


def test_with_namespace_adds_only_when_given():
    assert _with_namespace({"page_size": 5}, None) == {"page_size": 5}
    assert _with_namespace({"page_size": 5}, "ns-a") == {"page_size": 5, "namespace": "ns-a"}


@pytest.mark.asyncio
async def test_activity_passes_the_namespace_to_every_source(monkeypatch):
    svc = SearchService(None)
    seen: dict[str, object] = {}
    for name in ("terminologies", "terms", "templates", "documents", "files"):
        async def fetch(limit, namespace=None, _name=name):
            seen[_name] = namespace
            return []
        monkeypatch.setattr(svc, f"_get_recent_{name}", fetch)

    await svc.get_recent_activity(limit=5, namespace="ns-a")
    assert seen == dict.fromkeys(
        ("terminologies", "terms", "templates", "documents", "files"), "ns-a"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fetcher,url",
    [
        ("_get_recent_terminologies", f"{DEF}/api/def-store/terminologies"),
        ("_get_recent_templates", f"{TPL}/api/template-store/templates"),
        ("_get_recent_files", f"{DOC}/api/document-store/files"),
    ],
)
async def test_activity_sources_send_the_namespace_upstream(httpx_mock, fetcher, url):
    httpx_mock.add_response(url=re.compile(re.escape(url) + r"\?.*"), json={"items": []})
    await getattr(SearchService(None), fetcher)(5, "ns-a")
    request = httpx_mock.get_requests()[0]
    assert request.url.params["namespace"] == "ns-a"


@pytest.mark.asyncio
async def test_recent_documents_scope_both_the_template_map_and_the_documents(httpx_mock):
    httpx_mock.add_response(
        url=re.compile(re.escape(f"{TPL}/api/template-store/templates") + r"\?.*"),
        json={"items": []},
    )
    httpx_mock.add_response(
        url=re.compile(re.escape(f"{DOC}/api/document-store/documents") + r"\?.*"),
        json={"items": []},
    )
    await SearchService(None)._get_recent_documents(5, "ns-a")
    assert [r.url.params.get("namespace") for r in httpx_mock.get_requests()] == ["ns-a", "ns-a"]


def _pool_capturing(calls: list):
    conn = MagicMock()

    async def fetch(sql, *args):
        calls.append((sql, args))
        return []

    conn.fetch = AsyncMock(side_effect=fetch)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace", ["ns-a", None])
async def test_term_documents_scan_only_the_namespace_schema(namespace):
    calls: list = []
    await SearchService(_pool_capturing(calls)).get_term_documents("term-1", 10, namespace)
    sql, args = calls[0]
    assert "table_schema = $1" in sql
    assert args == (namespace,)


@pytest.mark.asyncio
async def test_referenced_by_passes_the_namespace_to_each_kind(monkeypatch):
    svc = SearchService(None)
    seen = {}
    for kind in ("template", "terminology", "term", "file"):
        async def handler(entity_id, limit, namespace=None, _kind=kind):
            seen[_kind] = namespace
            return MagicMock()
        monkeypatch.setattr(svc, f"_get_{kind}_referenced_by", handler)

    for kind in ("template", "terminology", "term", "file"):
        await svc.get_referenced_by(kind, "id-1", 10, namespace="ns-a")
    assert seen == dict.fromkeys(("template", "terminology", "term", "file"), "ns-a")


@pytest.mark.asyncio
async def test_file_references_hidden_outside_the_files_namespace(httpx_mock):
    httpx_mock.add_response(
        url=f"{DOC}/api/document-store/files/file-1",
        json={"file_id": "file-1", "namespace": "ns-b", "filename": "x.pdf"},
    )
    result = await SearchService(None)._get_file_referenced_by("file-1", 10, "ns-a")
    assert result.error and "not found" in result.error.lower()
    assert result.referenced_by == []
    # the file->documents listing was never asked
    assert len(httpx_mock.get_requests()) == 1
