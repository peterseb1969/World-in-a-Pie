"""CASE-848 — registered reporting views are manageable and readable through MCP.

Four tools over reporting-sync's view endpoints: register (POST), list (GET),
read rows (POST …/rows, the namespace-READ path that needs no SQL) and delete
(DELETE). The namespace is a path segment the server cannot derive from the
key, so it resolves explicit → WIP_MCP_DEFAULT_NAMESPACE → a clear error.
Bodies carry only the arguments the caller set (the endpoints' models are
strict). Requests go through a real httpx client on a MockTransport, so method,
URL and body are the ones actually sent.
"""

import json
from unittest.mock import patch

import httpx
import pytest

from wip_mcp import server
from wip_mcp.client import WipClient

BASE = "http://rs:8005"
VIEWS = "/api/reporting-sync/namespace/clinic-a/views"


def _client_recording(reply: dict | None = None, status: int = 200):
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status, json=reply if reply is not None else {})

    client = WipClient(reporting_sync_url=BASE, api_key="k")
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client, http, sent


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content) if request.content else {}


@pytest.mark.asyncio
async def test_register_posts_only_the_fields_set():
    client, http, sent = _client_recording({"created": True, "schema_qualified": '"clinic-a"."v1"'})
    with patch.object(client, "_get_client", return_value=http):
        result = await client.register_reporting_view("v1", "SELECT 1", namespace="clinic-a")
        await client.register_reporting_view("v1", "SELECT 1", namespace="clinic-a", registered_by="boot")

    assert (sent[0].method, sent[0].url.path) == ("POST", VIEWS)
    assert _body(sent[0]) == {"name": "v1", "sql": "SELECT 1"}
    assert _body(sent[1]) == {"name": "v1", "sql": "SELECT 1", "registered_by": "boot"}
    assert result == {"created": True, "schema_qualified": '"clinic-a"."v1"'}


@pytest.mark.asyncio
async def test_list_gets_the_namespace_views():
    client, http, sent = _client_recording({"namespace": "clinic-a", "views": []})
    with patch.object(client, "_get_client", return_value=http):
        result = await client.list_reporting_views(namespace="clinic-a")
    assert (sent[0].method, sent[0].url.path) == ("GET", VIEWS)
    assert result["views"] == []


@pytest.mark.asyncio
async def test_read_rows_sends_an_empty_body_by_default_and_only_set_fields_otherwise():
    client, http, sent = _client_recording({"columns": [], "rows": [], "row_count": 0, "truncated": False})
    with patch.object(client, "_get_client", return_value=http):
        await client.read_reporting_view("v1", namespace="clinic-a")
        await client.read_reporting_view(
            "v1", namespace="clinic-a",
            filters=[{"column": "status", "op": "eq", "value": "active"}], limit=50,
        )
    assert (sent[0].method, sent[0].url.path) == ("POST", f"{VIEWS}/v1/rows")
    assert _body(sent[0]) == {}
    assert _body(sent[1]) == {"filters": [{"column": "status", "op": "eq", "value": "active"}], "limit": 50}


@pytest.mark.asyncio
async def test_delete_sends_delete_on_the_view():
    client, http, sent = _client_recording({"namespace": "clinic-a", "name": "v1", "dropped": True})
    with patch.object(client, "_get_client", return_value=http):
        result = await client.delete_reporting_view("v1", namespace="clinic-a")
    assert (sent[0].method, sent[0].url.path) == ("DELETE", f"{VIEWS}/v1")
    assert result["dropped"] is True


@pytest.mark.asyncio
async def test_path_segments_are_encoded():
    client, http, sent = _client_recording()
    with patch.object(client, "_get_client", return_value=http):
        await client.delete_reporting_view("a/b", namespace="clinic-a")
    assert sent[0].url.raw_path.decode().endswith("/views/a%2Fb")


@pytest.mark.asyncio
async def test_namespace_defaults_to_the_mcp_default(monkeypatch):
    monkeypatch.setenv("WIP_MCP_DEFAULT_NAMESPACE", "clinic-a")
    client, http, sent = _client_recording({"views": []})
    with patch.object(client, "_get_client", return_value=http):
        await client.list_reporting_views()
    assert sent[0].url.path == VIEWS


@pytest.mark.asyncio
async def test_no_namespace_and_no_default_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("WIP_MCP_DEFAULT_NAMESPACE", raising=False)
    client, http, sent = _client_recording()
    with patch.object(client, "_get_client", return_value=http), pytest.raises(ValueError, match="namespace is required"):
        await client.list_reporting_views()
    assert sent == []


@pytest.mark.asyncio
async def test_tool_surfaces_the_servers_refusal_detail():
    detail = "View depends on relations outside the namespace schema: public._wip_views"
    client, http, _ = _client_recording({"detail": detail}, status=403)
    with patch.object(client, "_get_client", return_value=http), patch.object(server, "get_client", return_value=client):
        out = await server.register_reporting_view("v1", "SELECT * FROM public._wip_views", namespace="clinic-a")
    assert out.startswith("Error:")
    assert "403" in out and detail in out


@pytest.mark.asyncio
async def test_tools_return_the_server_json():
    client, http, _ = _client_recording({"columns": ["a"], "rows": [{"a": 1}], "row_count": 1, "truncated": False})
    with patch.object(client, "_get_client", return_value=http), patch.object(server, "get_client", return_value=client):
        out = await server.read_reporting_view("v1", namespace="clinic-a")
    assert json.loads(out)["rows"] == [{"a": 1}]


def test_register_and_delete_are_write_tools_list_and_read_are_not():
    assert {"register_reporting_view", "delete_reporting_view"} <= server.WRITE_TOOLS
    assert not {"list_reporting_views", "read_reporting_view"} & server.WRITE_TOOLS
