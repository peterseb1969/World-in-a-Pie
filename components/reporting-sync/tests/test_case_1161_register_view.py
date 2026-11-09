"""View registration, pinned before register_view's refactor.

Provenance: CASE-1161. The suite never reached these branches: an invalid
view name (400), a namespace whose schema cannot be named (400) or that was
deleted (404), no read pool (503), and — the containment rule — a
namespace-admin key's view that depends on a namespace the key cannot read
or on platform tables: 403 naming exactly those relations, and nothing is
registered.
"""

import contextlib
from unittest.mock import AsyncMock

import pytest

from reporting_sync import main
from reporting_sync.schema_manager import NamespaceDeletedError, SchemaManager

from .test_case_839_registered_views import http_client, mock_state  # noqa: F401  (fixtures)

PATH = "/api/reporting-sync/namespace/ns-a/views"


@pytest.mark.asyncio
async def test_an_invalid_view_name_is_400(http_client, mock_state):  # noqa: F811
    async with http_client:
        r = await http_client.post(PATH, json={"name": "v\"x", "sql": "SELECT 1"})
    assert r.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("error, status", [
    (ValueError("schema name not allowed"), 400),
    (NamespaceDeletedError("ns-a"), 404),
])
async def test_a_namespace_without_a_usable_schema(http_client, mock_state, monkeypatch, error, status):  # noqa: F811
    monkeypatch.setattr(SchemaManager, "ensure_schema", AsyncMock(side_effect=error))
    async with http_client:
        r = await http_client.post(PATH, json={"name": "v_x", "sql": "SELECT 1"})
    assert r.status_code == status
    if status == 404:
        assert r.json()["detail"] == "Namespace not found: ns-a"


@pytest.mark.asyncio
async def test_no_read_pool_is_503(http_client, mock_state, monkeypatch):  # noqa: F811
    _pool, conn = mock_state
    conn.fetchval = AsyncMock(side_effect=[False, False])
    monkeypatch.setattr(SchemaManager, "ensure_schema", AsyncMock(return_value="ns-a"))
    monkeypatch.setattr(main.state, "read_pool", None)
    async with http_client:
        r = await http_client.post(PATH, json={"name": "v_x", "sql": "SELECT 1"})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_a_namespace_admin_view_over_unreadable_relations_is_refused(
    http_client, mock_state, monkeypatch,  # noqa: F811
):
    _pool, conn = mock_state
    conn.fetchval = AsyncMock(side_effect=[False, False])  # not registered, name free
    conn.fetch = AsyncMock(side_effect=[
        [],  # the EXPLAIN on the caller's connection
        [{"schema": "ns-b", "relation": "doc_x"},  # foreign dependencies of the created view
         {"schema": "public", "relation": "_wip_views"},
         {"schema": "ns-c", "relation": "doc_y"}],
    ])
    monkeypatch.setattr(SchemaManager, "ensure_schema", AsyncMock(return_value="ns-a"))
    monkeypatch.setattr(main, "_is_privileged", lambda identity: False)

    async def permission(identity, ns, level):
        if not ((ns == "ns-a" and level == "admin") or (ns == "ns-c" and level == "read")):
            raise main.HTTPException(status_code=404, detail="Namespace not found")

    monkeypatch.setattr(main, "check_namespace_permission", permission)

    @contextlib.asynccontextmanager
    async def caller_conn(identity):
        yield conn

    monkeypatch.setattr(main, "_caller_read_connection", caller_conn)

    async with http_client:
        r = await http_client.post(PATH, json={"name": "v_x", "sql": "SELECT * FROM ns_b.doc_x"})
    assert r.status_code == 403
    assert "(ns-b.doc_x, public._wip_views)" in r.json()["detail"]
    executed = [str(c.args[0]) for c in conn.execute.call_args_list]
    assert any("CREATE VIEW" in s for s in executed)
    assert not any("_wip_views" in s and "INSERT" in s for s in executed)
