"""MCP transport regression tests — verify stdio, HTTP, and SSE all work.

Each transport is tested end-to-end: start the server, connect a real MCP
client, run a tool listing, and verify identical tool sets across transports.

These tests spawn subprocesses (stdio) or uvicorn servers (HTTP/SSE) to avoid
module-caching issues and to exercise the actual transport code paths.
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager, closing
from pathlib import Path

import pytest

MCP_SRC = str(Path(__file__).resolve().parent.parent / "src")


def _free_port() -> int:
    """Find a free TCP port on localhost."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 10.0) -> bool:
    """Wait until a TCP port is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with closing(socket.create_connection(("127.0.0.1", port), timeout=0.5)):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _base_env(**overrides) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = MCP_SRC
    env.pop("WIP_MCP_MODE", None)
    env.update(overrides)
    return env


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# Every variable the server takes its key from, plus the dev override. Stripped
# from the inherited environment so each test sets exactly the key it means.
_KEY_VARS = (
    "WIP_API_KEY", "MASTER_API_KEY", "API_KEY", "WIP_AUTH_LEGACY_API_KEY",
    "WIP_API_KEY_FILE", "MCP_ALLOW_UNAUTHENTICATED",
)


def _network_env(port: int, api_key: str | None, key_var: str) -> dict:
    env = _base_env(MCP_PORT=str(port), MCP_HOST="127.0.0.1")
    for var in _KEY_VARS:
        env.pop(var, None)
    if api_key:
        env[key_var] = api_key
    else:
        # Transport tests that do not exercise auth run with the explicit
        # local-development override; without a key the server refuses to start.
        env["MCP_ALLOW_UNAUTHENTICATED"] = "1"
    return env


@asynccontextmanager
async def _network_server(
    transport_flag: str, port: int, api_key: str | None = None, key_var: str = "API_KEY"
):
    """Start the MCP server with --http or --sse on a given port, yield, then kill.

    `key_var` names the env var carrying the key; the deployer provides
    MASTER_API_KEY, so tests of the deployed shape pass that.
    """
    env = _network_env(port, api_key, key_var)

    proc = subprocess.Popen(
        [sys.executable, "-m", "wip_mcp", transport_flag],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Wait for port to become available
        if not _wait_for_port(port, timeout=15.0):
            stderr = proc.stderr.read().decode() if proc.stderr else ""
            proc.kill()
            pytest.fail(f"Server did not start on port {port} within 15s. stderr: {stderr}")
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ---------------------------------------------------------------------------
# stdio transport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdio_list_tools():
    """stdio transport: connect, list tools, verify non-empty set."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "wip_mcp"],
        env=_base_env(),
    )

    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        tool_names = {t.name for t in tools.tools}
        assert len(tool_names) > 50, f"Expected 50+ tools, got {len(tool_names)}"
        assert "get_wip_status" in tool_names
        assert "describe_data_model" in tool_names


@pytest.mark.asyncio
async def test_stdio_list_resources():
    """stdio transport: list resources, verify query-assistant-prompt present."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "wip_mcp"],
        env=_base_env(),
    )

    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        resources = await session.list_resources()
        resource_uris = {str(r.uri) for r in resources.resources}
        assert "wip://query-assistant-prompt" in resource_uris


@pytest.mark.asyncio
async def test_stdio_readonly_mode():
    """stdio transport in readonly mode: write tools stripped."""
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "wip_mcp"],
        env=_base_env(WIP_MCP_MODE="readonly"),
    )

    async with (
        stdio_client(server_params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        tool_names = {t.name for t in tools.tools}
        # Write tools must be absent
        assert "create_terminology" not in tool_names
        assert "create_namespace" not in tool_names
        # Read tools must be present
        assert "get_wip_status" in tool_names
        assert "list_templates" in tool_names


# ---------------------------------------------------------------------------
# Streamable HTTP transport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_list_tools():
    """HTTP transport: connect, list tools, verify same set as stdio."""
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    port = _free_port()
    async with _network_server("--http", port):
        url = f"http://127.0.0.1:{port}/mcp"
        async with (
            streamable_http_client(url) as (read_stream, write_stream, _),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {t.name for t in tools.tools}
            assert len(tool_names) > 50
            assert "get_wip_status" in tool_names
            assert "describe_data_model" in tool_names


@pytest.mark.asyncio
async def test_http_list_resources():
    """HTTP transport: list resources."""
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    port = _free_port()
    async with _network_server("--http", port):
        url = f"http://127.0.0.1:{port}/mcp"
        async with (
            streamable_http_client(url) as (read_stream, write_stream, _),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            resources = await session.list_resources()
            resource_uris = {str(r.uri) for r in resources.resources}
            assert "wip://query-assistant-prompt" in resource_uris


@pytest.mark.asyncio
async def test_http_api_key_required():
    """HTTP transport with API_KEY set: requests without key get 401."""
    import httpx

    port = _free_port()
    api_key = "test-secret-key-12345"
    async with _network_server("--http", port, api_key=api_key), httpx.AsyncClient() as client:
        # No key → 401
        resp = await client.post(
            f"http://127.0.0.1:{port}/mcp",
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {}},
        )
        assert resp.status_code == 401

        # Wrong key → 401
        resp = await client.post(
            f"http://127.0.0.1:{port}/mcp",
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {}},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_http_health_exempt_from_api_key():
    """GET /health returns 200 without an API key — orchestration
    probes (compose healthcheck, k8s readinessProbe) must not need
    credentials."""
    import httpx

    port = _free_port()
    async with _network_server("--http", port, api_key="any-key"), httpx.AsyncClient() as client:
        resp = await client.get(f"http://127.0.0.1:{port}/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["service"] == "mcp-server"


@pytest.mark.asyncio
async def test_http_api_key_accepted():
    """HTTP transport: valid API key allows MCP handshake."""
    import httpx
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    port = _free_port()
    api_key = "test-secret-key-12345"
    async with _network_server("--http", port, api_key=api_key):
        url = f"http://127.0.0.1:{port}/mcp"
        http_client = httpx.AsyncClient(headers={"X-API-Key": api_key})
        async with streamable_http_client(url, http_client=http_client) as (
            read_stream, write_stream, _
        ), ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) > 50


# ---------------------------------------------------------------------------
# SSE transport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_list_tools():
    """SSE transport: connect, list tools, verify same set as stdio/HTTP."""
    from mcp.client.session import ClientSession
    from mcp.client.sse import sse_client

    port = _free_port()
    async with _network_server("--sse", port):
        url = f"http://127.0.0.1:{port}/sse"
        async with (
            sse_client(url) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            tool_names = {t.name for t in tools.tools}
            assert len(tool_names) > 50
            assert "get_wip_status" in tool_names
            assert "describe_data_model" in tool_names


@pytest.mark.asyncio
async def test_sse_list_resources():
    """SSE transport: list resources."""
    from mcp.client.session import ClientSession
    from mcp.client.sse import sse_client

    port = _free_port()
    async with _network_server("--sse", port):
        url = f"http://127.0.0.1:{port}/sse"
        async with (
            sse_client(url) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            resources = await session.list_resources()
            resource_uris = {str(r.uri) for r in resources.resources}
            assert "wip://query-assistant-prompt" in resource_uris


@pytest.mark.asyncio
async def test_sse_api_key_required():
    """SSE transport with API_KEY set: requests without key get 401."""
    import httpx

    port = _free_port()
    api_key = "test-secret-key-sse"
    async with _network_server("--sse", port, api_key=api_key), httpx.AsyncClient() as client:
        resp = await client.get(f"http://127.0.0.1:{port}/sse")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Cross-transport consistency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_transports_same_tools():
    """All three transports expose the exact same tool set."""
    from mcp.client.session import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamable_http_client

    # stdio
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "wip_mcp"],
        env=_base_env(),
    )
    async with stdio_client(server_params) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        stdio_tools = {t.name for t in (await session.list_tools()).tools}

    # HTTP
    http_port = _free_port()
    async with _network_server("--http", http_port):
        url = f"http://127.0.0.1:{http_port}/mcp"
        async with streamable_http_client(url) as (r, w, _), ClientSession(r, w) as session:
            await session.initialize()
            http_tools = {t.name for t in (await session.list_tools()).tools}

    # SSE
    sse_port = _free_port()
    async with _network_server("--sse", sse_port):
        url = f"http://127.0.0.1:{sse_port}/sse"
        async with sse_client(url) as (r, w), ClientSession(r, w) as session:
            await session.initialize()
            sse_tools = {t.name for t in (await session.list_tools()).tools}

    # All three must match
    assert stdio_tools == http_tools, (
        f"stdio vs HTTP diff: {stdio_tools.symmetric_difference(http_tools)}"
    )
    assert stdio_tools == sse_tools, (
        f"stdio vs SSE diff: {stdio_tools.symmetric_difference(sse_tools)}"
    )


@pytest.mark.asyncio
async def test_all_transports_same_resources():
    """All three transports expose the exact same resource set."""
    from mcp.client.session import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import streamable_http_client

    # stdio
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "wip_mcp"],
        env=_base_env(),
    )
    async with stdio_client(server_params) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        stdio_res = {str(r.uri) for r in (await session.list_resources()).resources}

    # HTTP
    http_port = _free_port()
    async with _network_server("--http", http_port):
        url = f"http://127.0.0.1:{http_port}/mcp"
        async with streamable_http_client(url) as (r, w, _), ClientSession(r, w) as session:
            await session.initialize()
            http_res = {str(r.uri) for r in (await session.list_resources()).resources}

    # SSE
    sse_port = _free_port()
    async with _network_server("--sse", sse_port):
        url = f"http://127.0.0.1:{sse_port}/sse"
        async with sse_client(url) as (r, w), ClientSession(r, w) as session:
            await session.initialize()
            sse_res = {str(r.uri) for r in (await session.list_resources()).resources}

    assert stdio_res == http_res, (
        f"stdio vs HTTP diff: {stdio_res.symmetric_difference(http_res)}"
    )
    assert stdio_res == sse_res, (
        f"stdio vs SSE diff: {stdio_res.symmetric_difference(sse_res)}"
    )


# ---------------------------------------------------------------------------
# Port / host configuration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_custom_port():
    """MCP_PORT env var controls the HTTP bind port."""
    port = _free_port()
    async with _network_server("--http", port):
        # If we got here, the server is listening on the custom port
        assert _wait_for_port(port, timeout=1.0)


def test_http_without_key_refuses_to_start():
    """No configured key and no override: the server exits instead of serving
    every caller with its own downstream credentials."""
    env = _network_env(_free_port(), None, "API_KEY")
    env.pop("MCP_ALLOW_UNAUTHENTICATED")
    proc = subprocess.run(
        [sys.executable, "-m", "wip_mcp", "--http"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode != 0
    assert "refusing to serve" in proc.stderr
    assert "MCP_ALLOW_UNAUTHENTICATED" in proc.stderr


@pytest.mark.asyncio
async def test_http_dev_override_starts_with_a_loud_warning():
    """MCP_ALLOW_UNAUTHENTICATED=1 starts without a key and says so."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "wip_mcp", "--http"],
        env=_network_env(port, None, "API_KEY"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert _wait_for_port(port, timeout=15.0)
        await asyncio.sleep(0.5)
        proc.terminate()
        proc.wait(timeout=5)
        stderr = proc.stderr.read().decode()
        assert "WITHOUT authentication" in stderr, stderr
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


_INIT = {"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {
    "protocolVersion": "2025-03-26", "capabilities": {},
    "clientInfo": {"name": "auth-test", "version": "0"},
}}
_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


@pytest.mark.asyncio
async def test_http_deployed_key_name_is_enforced():
    """The deployer provides the key ONLY as MASTER_API_KEY. A server started
    that way must refuse an anonymous handshake and accept the right key."""
    import httpx

    port = _free_port()
    key = "deployed-master-key-123"
    async with _network_server("--http", port, api_key=key, key_var="MASTER_API_KEY"):
        url = f"http://127.0.0.1:{port}/mcp"
        async with httpx.AsyncClient() as client:
            anonymous = await client.post(url, json=_INIT, headers=_MCP_HEADERS)
            assert anonymous.status_code == 401
            ok = await client.post(url, json=_INIT, headers={**_MCP_HEADERS, "X-API-Key": key})
            assert ok.status_code == 200


@pytest.mark.asyncio
async def test_http_key_in_query_string_is_refused():
    """Header only — a key in the URL would land in access and proxy logs."""
    import httpx

    port = _free_port()
    key = "deployed-master-key-123"
    async with (
        _network_server("--http", port, api_key=key, key_var="MASTER_API_KEY"),
        httpx.AsyncClient() as client,
    ):
        resp = await client.post(
            f"http://127.0.0.1:{port}/mcp?api_key={key}", json=_INIT, headers=_MCP_HEADERS,
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_http_dev_default_key_is_never_accepted():
    """The outbound fallback key is public; it must not authenticate inbound."""
    import httpx

    port = _free_port()
    async with (
        _network_server("--http", port, api_key="real-key", key_var="MASTER_API_KEY"),
        httpx.AsyncClient() as client,
    ):
        resp = await client.post(
            f"http://127.0.0.1:{port}/mcp", json=_INIT,
            headers={**_MCP_HEADERS, "X-API-Key": "dev_master_key_for_testing"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sse_deployed_key_name_is_enforced():
    """Same contract on the SSE transport, started the deployer's way."""
    import httpx

    port = _free_port()
    async with (
        _network_server("--sse", port, api_key="sse-key", key_var="MASTER_API_KEY"),
        httpx.AsyncClient() as client,
    ):
        resp = await client.get(f"http://127.0.0.1:{port}/sse")
        assert resp.status_code == 401
