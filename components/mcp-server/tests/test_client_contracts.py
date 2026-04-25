"""Contract tests for wip_mcp.client + wip_mcp.server tool functions.

Each test maps 1:1 to a bug shipped in the 2026-04-22..24 window:

  1. `test_docstring_env_var_drift`          — I documented `MASTER_API_KEY`
                                                in the WipClient docstring
                                                and proudly shipped a config
                                                using it. The name was never
                                                read by any code. This test
                                                asserts the docstring names
                                                exactly match the env vars
                                                read by the module.

  2. `test_resolve_api_key_contract`         — Same root cause: `MASTER_API_KEY`
                                                invented as a fallback that
                                                doesn't exist. This test pins
                                                the exact resolution order of
                                                `_resolve_api_key`.

  3. `test_url_construction_no_doubling`     — I told another YAC to set
                                                `REGISTRY_URL=…/api/registry`
                                                and the client silently built
                                                `…/api/registry/api/registry/…`.
                                                This test codifies "base URL
                                                is a root; client owns the
                                                api-prefix" so user-provided
                                                api-prefixed URLs fail loudly
                                                (doubling is detectable) rather
                                                than silently.

  F. `test_check_health_uses_api_prefix`     — `check_health` used to GET
                                                `{url}/health` at the service
                                                root. Through Caddy that hit
                                                an unrouted path returning
                                                200 empty, and resp.json()
                                                exploded. This test asserts
                                                the api-prefixed form is used.

  G. `test_get_wip_status_tool_e2e`          — The handshake-level smoke I
                                                relied on ("initialize works")
                                                didn't touch backends at all,
                                                so the `check_health` bug
                                                went undetected. This test
                                                drives a tool function against
                                                a mocked backend and asserts
                                                the user-visible return value
                                                reports success — catching
                                                "server boots, first real tool
                                                call explodes."
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import wip_mcp.client as client_module
import wip_mcp.server as server_module
from wip_mcp.client import WipClient, _resolve_api_key

_CLIENT_SOURCE_PATH = Path(client_module.__file__)


# =========================================================================
# Test 1 — Docstring ↔ source env-var drift
# =========================================================================


def _env_vars_read_in_module(source_path: Path) -> set[str]:
    """Return every env var name passed to os.getenv() in a module."""
    tree = ast.parse(source_path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getenv"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.add(node.args[0].value)
    return names


def _env_vars_documented_in_client_py() -> set[str]:
    """Extract env-var names mentioned in any docstring in client.py.

    Uses ALL_CAPS_WORD pattern — env vars follow the convention, so this
    is a deliberate heuristic: any uppercase identifier is treated as
    an env-var reference.
    """
    source = _CLIENT_SOURCE_PATH.read_text()
    # Collect text of every docstring (module-level, class, function)
    tree = ast.parse(source)
    docstrings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(
            node,
            (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
        ):
            ds = ast.get_docstring(node)
            if ds:
                docstrings.append(ds)
    combined = "\n".join(docstrings)
    # All-caps tokens ≥4 chars, underscore-joined — matches env-var names.
    candidates = set(re.findall(r"\b[A-Z][A-Z_]{3,}\b", combined))
    # Filter to WIP_-prefixed names + a few known externals we reference
    # generically. This keeps the test focused on OUR env-var surface.
    return {c for c in candidates if c.startswith("WIP_")}


def test_docstring_env_var_drift() -> None:
    """Every env var read in client.py must be named in a docstring,
    and every WIP_*-prefixed name in a docstring must correspond to a
    real getenv call. Catches invented names and undocumented reads."""
    read = {n for n in _env_vars_read_in_module(_CLIENT_SOURCE_PATH) if n.startswith("WIP_")}
    documented = _env_vars_documented_in_client_py()
    missing_docs = read - documented
    phantom_docs = documented - read
    assert not missing_docs, (
        f"env vars read but not documented: {missing_docs}"
    )
    assert not phantom_docs, (
        f"env vars documented but not read (likely invented): {phantom_docs}"
    )


# =========================================================================
# Test 2 — _resolve_api_key contract
# =========================================================================


_DEV_DEFAULT = "dev_master_key_for_testing"


@pytest.fixture
def clean_api_key_env(monkeypatch):
    """Ensure none of the api-key-related env vars leak in from the shell."""
    for var in ("WIP_API_KEY", "WIP_API_KEY_FILE", "MASTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    yield monkeypatch


def test_resolve_api_key_reads_WIP_API_KEY(clean_api_key_env) -> None:
    clean_api_key_env.setenv("WIP_API_KEY", "the-real-key")
    assert _resolve_api_key() == "the-real-key"


def test_resolve_api_key_reads_WIP_API_KEY_FILE(clean_api_key_env, tmp_path) -> None:
    key_file = tmp_path / "api.key"
    key_file.write_text("  key-from-file\n")  # leading/trailing whitespace stripped
    clean_api_key_env.setenv("WIP_API_KEY_FILE", str(key_file))
    assert _resolve_api_key() == "key-from-file"


def test_resolve_api_key_defaults_when_nothing_set(clean_api_key_env) -> None:
    # No env vars set — fall through to dev default.
    assert _resolve_api_key() == _DEV_DEFAULT


def test_resolve_api_key_does_NOT_read_MASTER_API_KEY(clean_api_key_env) -> None:
    """Explicit negative: MASTER_API_KEY is NOT a fallback for WIP_API_KEY.

    Regression guard against the specific invention shipped in the
    2026-04-24 stdio config. If someone later decides MASTER_API_KEY
    should also be honored, this test forces the decision to be
    deliberate (update _resolve_api_key AND flip this assertion)
    rather than accidental ambient coupling.
    """
    clean_api_key_env.setenv("MASTER_API_KEY", "should-be-ignored")
    # With no WIP_API_KEY / WIP_API_KEY_FILE, we still get the default.
    assert _resolve_api_key() == _DEV_DEFAULT


def test_resolve_api_key_WIP_API_KEY_wins_over_FILE(clean_api_key_env, tmp_path) -> None:
    """When both set, WIP_API_KEY env wins (documented priority order)."""
    key_file = tmp_path / "api.key"
    key_file.write_text("from-file")
    clean_api_key_env.setenv("WIP_API_KEY", "from-env")
    clean_api_key_env.setenv("WIP_API_KEY_FILE", str(key_file))
    assert _resolve_api_key() == "from-env"


# =========================================================================
# Test 3 — URL construction: no doubling
# =========================================================================


def _make_client(**kwargs) -> WipClient:
    defaults = {
        "registry_url": "http://test:8001",
        "def_store_url": "http://test:8002",
        "template_store_url": "http://test:8003",
        "document_store_url": "http://test:8004",
        "reporting_sync_url": "http://test:8005",
        "api_key": "test_key",
        "verify_tls": True,
    }
    defaults.update(kwargs)
    return WipClient(**defaults)


def _capture_urls_mock() -> AsyncMock:
    """Mock httpx.AsyncClient that records every URL passed to .get / .post."""
    mock = AsyncMock()
    resp = MagicMock()
    resp.json.return_value = {"items": [], "total": 0, "page": 1, "pages": 0}
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    mock.get.return_value = resp
    mock.post.return_value = resp
    return mock


@pytest.mark.asyncio
async def test_url_construction_root_url_prepends_api_prefix() -> None:
    """Happy path: user supplies a root URL, client adds /api/<service>/."""
    client = _make_client(registry_url="https://localhost:8443")
    mock_http = _capture_urls_mock()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.list_namespaces()
    called_url = mock_http.get.call_args.args[0]
    assert called_url == "https://localhost:8443/api/registry/namespaces", called_url


@pytest.mark.asyncio
async def test_url_construction_api_prefixed_url_doubles_detectably() -> None:
    """If a user hands us an api-prefixed URL (the mistake from the
    2026-04-24 stdio config), the client produces a doubled path. This
    test codifies that — so if someone later adds normalization they
    see this test fail and make the tradeoff deliberately."""
    client = _make_client(registry_url="https://localhost:8443/api/registry")
    mock_http = _capture_urls_mock()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.list_namespaces()
    called_url = mock_http.get.call_args.args[0]
    assert called_url == "https://localhost:8443/api/registry/api/registry/namespaces", (
        called_url
    )


@pytest.mark.asyncio
async def test_url_construction_trailing_slash_does_not_break() -> None:
    """Trailing slash on a root URL: client concatenates as-is; produces
    one extra slash but the resulting URL still works (HTTP normalizes //).
    Pinning current behavior so changes are intentional."""
    client = _make_client(registry_url="https://localhost:8443/")
    mock_http = _capture_urls_mock()
    with patch.object(client, "_get_client", return_value=mock_http):
        await client.list_namespaces()
    called_url = mock_http.get.call_args.args[0]
    assert called_url == "https://localhost:8443//api/registry/namespaces", called_url


# =========================================================================
# Test F — check_health probes api-prefixed paths
# =========================================================================


@pytest.mark.asyncio
async def test_check_health_uses_api_prefixed_paths() -> None:
    """check_health must GET /api/<service>/health for every service —
    not the bare /health that failed through Caddy in the 2026-04-24
    stdio-MCP regression. Direct guard for the exact fix committed
    as part of that debug cycle."""
    client = _make_client(
        registry_url="https://h:8443",
        def_store_url="https://h:8443",
        template_store_url="https://h:8443",
        document_store_url="https://h:8443",
        reporting_sync_url="https://h:8443",
    )
    # Every service returns a minimal healthy JSON.
    mock_http = AsyncMock()
    resp = MagicMock()
    resp.json.return_value = {"status": "healthy"}
    resp.status_code = 200
    mock_http.get.return_value = resp
    with patch.object(client, "_get_client", return_value=mock_http):
        results = await client.check_health()

    urls_called = [call.args[0] for call in mock_http.get.call_args_list]
    expected = {
        "https://h:8443/api/registry/health",
        "https://h:8443/api/def-store/health",
        "https://h:8443/api/template-store/health",
        "https://h:8443/api/document-store/health",
        "https://h:8443/api/reporting-sync/health",
    }
    assert set(urls_called) == expected, f"got {urls_called}"
    # And all report healthy
    assert all(v["healthy"] for v in results.values()), results


@pytest.mark.asyncio
async def test_check_health_marks_service_unhealthy_on_non_200() -> None:
    """Non-2xx response → healthy:false. Preserves the reporting
    contract callers depend on (notably get_wip_status)."""
    client = _make_client(registry_url="https://h:8443")
    mock_http = AsyncMock()
    # First service returns 500, rest 200 — only registry should be unhealthy.
    good = MagicMock()
    good.status_code = 200
    good.json.return_value = {"ok": True}
    bad = MagicMock()
    bad.status_code = 500
    mock_http.get.side_effect = [bad, good, good, good, good]
    with patch.object(client, "_get_client", return_value=mock_http):
        results = await client.check_health()
    assert results["registry"]["healthy"] is False
    assert all(results[s]["healthy"] for s in
               ("def_store", "template_store", "document_store", "reporting_sync"))


# =========================================================================
# Test G — tool-call E2E (in-process) with mocked backend
# =========================================================================


@pytest.mark.asyncio
async def test_get_wip_status_tool_reports_all_healthy() -> None:
    """Drive the get_wip_status tool function end-to-end against a
    mocked backend. The tool function:

      get_wip_status → get_client().check_health() → httpx calls to
      /api/<service>/health

    This was failing in production (returning "DOWN / Expecting value")
    because check_health probed /health at the service root, which
    Caddy 200-empty'd. Catches the full class "tool imports fine but
    blows up on first real request."
    """
    # Reset the module-level client singleton so our mocked WipClient
    # gets picked up by get_client().
    server_module._client = None
    mock_http = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"status": "healthy"}
    mock_http.get.return_value = resp
    fake = _make_client(
        registry_url="https://h:8443", def_store_url="https://h:8443",
        template_store_url="https://h:8443", document_store_url="https://h:8443",
        reporting_sync_url="https://h:8443",
    )
    with (
        patch.object(fake, "_get_client", return_value=mock_http),
        patch.object(server_module, "get_client", return_value=fake),
    ):
        result_text = await server_module.get_wip_status()

    # User-visible assertions — what APP-CT-YAC would see.
    assert "overall: all healthy" in result_text, result_text
    for svc in ("registry", "def_store", "template_store",
                "document_store", "reporting_sync"):
        assert f"{svc}: healthy" in result_text, f"{svc} missing in: {result_text}"


@pytest.mark.asyncio
async def test_list_namespaces_tool_returns_namespace_data() -> None:
    """list_namespaces tool returns JSON-formatted namespace data from
    the mocked registry. Proves the tool-layer → client-layer →
    backend-mock chain works end-to-end."""
    server_module._client = None
    mock_http = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = [
        {"prefix": "wip", "description": "Default", "status": "active"},
        {"prefix": "demo", "description": "Demo ns", "status": "active"},
    ]
    resp.raise_for_status = MagicMock()
    mock_http.get.return_value = resp
    fake = _make_client(registry_url="https://h:8443")
    with (
        patch.object(fake, "_get_client", return_value=mock_http),
        patch.object(server_module, "get_client", return_value=fake),
    ):
        result = await server_module.list_namespaces()
    # Tool returns a JSON string — parse and verify shape
    import json as _json
    parsed = _json.loads(result) if isinstance(result, str) else result
    assert len(parsed) == 2
    assert {ns["prefix"] for ns in parsed} == {"wip", "demo"}


# =========================================================================
# Test H — term-relations rename: old names are gone, new names exist
# =========================================================================
#
# The Phase-0 rename of the def-store ontology API moved
# `create_relationships` / `list_relationships` / `delete_relationships` to
# `*_term_relations` to avoid future collision with document-relationship
# tools. This test pins the new names and asserts the old ones are absent —
# so a future agent who pattern-matches "create_relationships" from training
# data and re-introduces it gets a hard fail instead of a half-renamed surface.


def test_term_relations_tool_names_replaced_old_ones() -> None:
    """The renamed term-relation tools exist on server + client; the old
    names do not. Regression guard for the Phase-0 rename."""
    new_names = ("create_term_relations", "list_term_relations",
                 "delete_term_relations")
    old_names = ("create_relationships", "list_relationships",
                 "delete_relationships")

    for name in new_names:
        assert hasattr(server_module, name), (
            f"server.py missing renamed tool: {name}"
        )
        assert hasattr(WipClient, name), (
            f"WipClient missing renamed method: {name}"
        )

    for name in old_names:
        assert not hasattr(server_module, name), (
            f"server.py still defines old tool name: {name}"
        )
        assert not hasattr(WipClient, name), (
            f"WipClient still defines old method name: {name}"
        )


def test_term_relations_url_path_uses_kebab_form() -> None:
    """The HTTP path written into client methods must be the new
    `/ontology/term-relations` form, not the old `/ontology/relationships`."""
    source = _CLIENT_SOURCE_PATH.read_text()
    assert "/ontology/term-relations" in source, (
        "client.py must reference /ontology/term-relations"
    )
    assert "/ontology/relationships" not in source, (
        "client.py still references the old /ontology/relationships path"
    )


# =========================================================================
# Test I — Phase 5 document-relationship MCP tools exist
# =========================================================================
#
# Phase 5 added two MCP wrappers over the Phase-4 document-store endpoints:
# get_document_relationships and traverse_documents. These tests pin the
# names and confirm the URL paths land on the expected document-store
# routes (regression guard against future renames or accidental drift).


def test_document_relationship_tools_exist() -> None:
    """Both Phase-5 tools must be registered on server and client."""
    for name in ("get_document_relationships", "traverse_documents"):
        assert hasattr(server_module, name), f"server.py missing tool: {name}"
        assert hasattr(WipClient, name), f"WipClient missing method: {name}"


def test_document_relationship_url_paths_match_phase4_routes() -> None:
    """Client methods must call the document-store routes Phase 4 added."""
    source = _CLIENT_SOURCE_PATH.read_text()
    assert "/api/document-store/documents/{document_id}/relationships" in source, (
        "client.py must reference /api/document-store/documents/{document_id}/relationships"
    )
    assert "/api/document-store/documents/{document_id}/traverse" in source, (
        "client.py must reference /api/document-store/documents/{document_id}/traverse"
    )
