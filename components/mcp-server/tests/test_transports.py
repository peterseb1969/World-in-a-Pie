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


@asynccontextmanager
async def _network_server(transport_flag: str, port: int, api_key: str | None = None):
    """Start the MCP server with --http or --sse on a given port, yield, then kill."""
    env = _base_env(MCP_PORT=str(port), MCP_HOST="127.0.0.1")
    if api_key:
        env["API_KEY"] = api_key

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

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
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

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
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

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
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
        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
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
        async with streamable_http_client(url) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
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
    async with _network_server("--http", port, api_key=api_key):
        async with httpx.AsyncClient() as client:
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
    async with _network_server("--http", port, api_key="any-key"):
        async with httpx.AsyncClient() as client:
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
        ):
            async with ClientSession(read_stream, write_stream) as session:
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
        async with sse_client(url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
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
        async with sse_client(url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
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
    async with _network_server("--sse", port, api_key=api_key):
        async with httpx.AsyncClient() as client:
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
    async with stdio_client(server_params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            stdio_tools = {t.name for t in (await session.list_tools()).tools}

    # HTTP
    http_port = _free_port()
    async with _network_server("--http", http_port):
        url = f"http://127.0.0.1:{http_port}/mcp"
        async with streamable_http_client(url) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                http_tools = {t.name for t in (await session.list_tools()).tools}

    # SSE
    sse_port = _free_port()
    async with _network_server("--sse", sse_port):
        url = f"http://127.0.0.1:{sse_port}/sse"
        async with sse_client(url) as (r, w):
            async with ClientSession(r, w) as session:
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
    async with stdio_client(server_params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            stdio_res = {str(r.uri) for r in (await session.list_resources()).resources}

    # HTTP
    http_port = _free_port()
    async with _network_server("--http", http_port):
        url = f"http://127.0.0.1:{http_port}/mcp"
        async with streamable_http_client(url) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                http_res = {str(r.uri) for r in (await session.list_resources()).resources}

    # SSE
    sse_port = _free_port()
    async with _network_server("--sse", sse_port):
        url = f"http://127.0.0.1:{sse_port}/sse"
        async with sse_client(url) as (r, w):
            async with ClientSession(r, w) as session:
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


@pytest.mark.asyncio
async def test_http_no_api_key_warning():
    """HTTP without API_KEY prints a warning to stderr."""
    port = _free_port()
    env = _base_env(MCP_PORT=str(port), MCP_HOST="127.0.0.1")
    env.pop("API_KEY", None)
    env.pop("WIP_AUTH_LEGACY_API_KEY", None)

    proc = subprocess.Popen(
        [sys.executable, "-m", "wip_mcp", "--http"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port, timeout=15.0)
        # Read stderr for warning
        # Give a moment for stderr to flush
        await asyncio.sleep(0.5)
        proc.terminate()
        proc.wait(timeout=5)
        stderr = proc.stderr.read().decode()
        assert "WARNING" in stderr, f"Expected WARNING in stderr, got: {stderr}"
        assert "API key" in stderr.lower() or "API_KEY" in stderr
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
