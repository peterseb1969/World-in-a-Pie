"""An administrator's unscoped listing reads only the namespaces it can read.

Provenance: CASE-1226. reporting-sync read every namespace schema for an
administrator who named no namespace, so a namespace locked for deletion
stayed in its search, term-documents, referenced-by and table listings. The
Registry now answers an administrator with every namespace but a locked one,
and those reads keep to that list.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import wip_auth.permissions as permissions
from reporting_sync.main import _effective_namespace, _listed_relation
from reporting_sync.search_service import SearchService, _document_tables
from wip_auth.models import UserIdentity

ADMIN = UserIdentity(
    user_id="apikey:legacy", username="legacy", auth_method="api_key", groups=["wip-admins"],
)


@pytest.fixture(autouse=True)
def _fresh_permission_cache():
    # Answers patched here must not outlive the test in the shared cache.
    permissions.clear_permission_cache()
    yield
    permissions.clear_permission_cache()


def _conn_capturing(calls: list):
    conn = MagicMock()

    async def fetch(sql, *args, **kwargs):
        calls.append((sql, args))
        return []

    conn.fetch = AsyncMock(side_effect=fetch)
    return conn


@pytest.mark.asyncio
async def test_an_administrator_without_a_namespace_lists_its_readable_ones(monkeypatch):
    async def accessible(identity):
        return ["open-a", "open-b"]  # "doomed" is locked: the Registry left it out

    monkeypatch.setattr(permissions, "_fetch_accessible_from_registry", accessible)
    assert await _effective_namespace(ADMIN, None) == (None, ["open-a", "open-b"])


def test_a_table_outside_the_readable_namespaces_is_not_listed():
    assert _listed_relation("open-a", "doc_x", None, None, ["open-a"]) is True
    assert _listed_relation("doomed", "doc_x", None, None, ["open-a"]) is False


@pytest.mark.asyncio
async def test_document_tables_keep_to_the_readable_schemas():
    calls: list = []
    await _document_tables(_conn_capturing(calls), None, ["open-a"])
    sql, args = calls[0]
    assert "ANY($1::text[])" in sql
    assert args == (["open-a"],)


@pytest.mark.asyncio
async def test_term_documents_keep_to_the_readable_schemas(monkeypatch):
    from reporting_sync import search_service

    async def term(client, url):
        return {"term_id": "term-1"}

    monkeypatch.setattr(search_service, "_fetch_target", term)
    calls: list = []
    conn = _conn_capturing(calls)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    await SearchService(pool).get_term_documents("term-1", 10, None, ["open-a"])
    _sql, args = calls[0]
    assert args == ("term_references_json", None, ["open-a"])
