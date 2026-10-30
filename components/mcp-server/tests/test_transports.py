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
from contextlib import asynccontextmanager, closing, contextmanager
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


@contextmanager
def _fake_registry(accepted: set[str], seen: list[tuple[str, str]] | None = None):
    """A stand-in Registry for the server's inbound key check: it lists
    namespaces (200) for the accepted keys and answers 401 to any other.
    `seen` records (path, X-API-Key) of every request it gets."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if seen is not None:
                seen.append((self.path, self.headers.get("X-API-Key", "")))
            ok = (
                self.path.startswith("/api/registry/namespaces")
                and self.headers.get("X-API-Key") in accepted
            )
            body = b"[]" if ok else b'{"detail": "Invalid API key"}'
            self.send_response(200 if ok else 401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def _network_env(
    port: int,
    api_key: str | None,
    key_var: str,
    registry_url: str | None = None,
    unauthenticated: bool | None = None,
) -> dict:
    env = _base_env(MCP_PORT=str(port), MCP_HOST="127.0.0.1")
    for var in _KEY_VARS:
        env.pop(var, None)
    env.pop("WIP_API_URL", None)
    if registry_url:
        env["REGISTRY_URL"] = registry_url
    if api_key:
        env[key_var] = api_key
    if unauthenticated if unauthenticated is not None else not api_key:
        # Transport tests that do not exercise auth run with the explicit
        # local-development override.
        env["MCP_ALLOW_UNAUTHENTICATED"] = "1"
    return env


@asynccontextmanager
async def _network_server(
    transport_flag: str,
    port: int,
    api_key: str | None = None,
    key_var: str = "API_KEY",
    accepted: set[str] | None = None,
    seen: list[tuple[str, str]] | None = None,
):
    """Start the MCP server with --http or --sse on a given port, yield, then kill.

    `key_var` names the env var carrying the key; the deployer provides
    MASTER_API_KEY, so tests of the deployed shape pass that. A fake Registry
    answers the server's inbound key check, accepting `accepted` (default:
    the configured key). Passing `accepted` runs authenticated even without
    a configured key.
    """
    with _fake_registry(
        accepted if accepted is not None else {api_key} - {None}, seen
    ) as registry:
        env = _network_env(
            port, api_key, key_var, registry,
            unauthenticated=False if accepted is not None else None,
        )
        async with _spawned_server(transport_flag, env, port) as proc:
            yield proc


@asynccontextmanager
async def _spawned_server(transport_flag: str, env: dict, port: int):
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


@pytest.mark.asyncio
async def test_http_tool_calls_act_with_the_callers_key():
    """A tool call reaches the backend with the key the MCP caller presented,
    never with the server's own (privileged) key."""
    import httpx
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    port = _free_port()
    seen: list[tuple[str, str]] = []
    async with _network_server(
        "--http", port, api_key="server-master-key", key_var="MASTER_API_KEY",
        accepted={"app-scoped-key"}, seen=seen,
    ):
        url = f"http://127.0.0.1:{port}/mcp"
        http_client = httpx.AsyncClient(headers={"X-API-Key": "app-scoped-key"})
        async with streamable_http_client(url, http_client=http_client) as (
            read_stream, write_stream, _
        ), ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            seen.clear()  # drop the inbound key checks
            result = await session.call_tool("list_namespaces", {})
            assert not result.isError, result
    assert seen, "the tool call never reached the backend"
    assert {key for _path, key in seen} == {"app-scoped-key"}, seen


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


@pytest.mark.asyncio
async def test_sse_tool_calls_act_with_the_callers_key():
    """On SSE the key arrives on each message POST, not only on the stream."""
    from mcp.client.session import ClientSession
    from mcp.client.sse import sse_client

    port = _free_port()
    seen: list[tuple[str, str]] = []
    async with _network_server(
        "--sse", port, api_key="server-master-key", key_var="MASTER_API_KEY",
        accepted={"app-scoped-key"}, seen=seen,
    ):
        url = f"http://127.0.0.1:{port}/sse"
        async with sse_client(url, headers={"X-API-Key": "app-scoped-key"}) as (r, w), \
                ClientSession(r, w) as session:
            await session.initialize()
            seen.clear()
            result = await session.call_tool("list_namespaces", {})
            assert not result.isError, result
    assert seen, "the tool call never reached the backend"
    assert {key for _path, key in seen} == {"app-scoped-key"}, seen


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


@pytest.mark.asyncio
async def test_http_serves_any_registry_accepted_key_without_its_own():
    """The server authenticates each caller with the Registry and acts with
    the caller's key, so it needs no key of its own: an app's scoped key is
    let in, a key the Registry refuses is not, and an anonymous call never is."""
    import httpx

    port = _free_port()
    async with _network_server("--http", port, api_key=None, accepted={"app-scoped-key"}):
        url = f"http://127.0.0.1:{port}/mcp"
        async with httpx.AsyncClient() as client:
            ok = await client.post(
                url, json=_INIT, headers={**_MCP_HEADERS, "X-API-Key": "app-scoped-key"}
            )
            assert ok.status_code == 200
            forged = await client.post(
                url, json=_INIT, headers={**_MCP_HEADERS, "X-API-Key": "forged"}
            )
            assert forged.status_code == 401
            anonymous = await client.post(url, json=_INIT, headers=_MCP_HEADERS)
            assert anonymous.status_code == 401


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
