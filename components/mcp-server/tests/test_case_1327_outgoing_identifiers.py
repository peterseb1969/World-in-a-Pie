"""An identifier an MCP tool sends on to a WIP service is one URL path
segment, whatever characters the value holds.

Agents pass identifiers as they have them: a canonical id, a value, a
'ns:VALUE' form, a synonym. A term value has no character rule ('#', '?'
and '%' are storable), so neither do the value forms that can name one.
Interpolated raw, an identifier is cut at '#' or '?', or read across
segments at '/', and the service answers about another one. Namespace
prefixes, backup job ids and replay session ids are left as they are: the
Registry restricts prefixes to one safe token, and the services mint the
others. An API key name is not restricted that way ('#' and '?' are legal),
so it is encoded like every other identifier.
"""

from __future__ import annotations

import ast
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

from wip_mcp import client as client_module
from wip_mcp.client import WipClient

ODD = "ns:A#1?b/c"


@pytest.fixture
def recorded(monkeypatch):
    monkeypatch.setenv("WIP_API_KEY", "k")
    client = WipClient()
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path.decode().split("?", 1)[0])
        return httpx.Response(200, json={})

    client._client = httpx.AsyncClient(
        auth=client_module._ActingKeyAuth(client), transport=httpx.MockTransport(handler)
    )
    return client, paths


@pytest.mark.asyncio
@pytest.mark.parametrize(("call", "route"), [
    (lambda c: c.get_entry(ODD), "/api/registry/entries/"),
    (lambda c: c.get_terminology(ODD), "/api/def-store/terminologies/"),
    (lambda c: c.get_terminology_by_value(ODD), "/api/def-store/terminologies/by-value/"),
    (lambda c: c.get_template(ODD), "/api/template-store/templates/"),
    (lambda c: c.get_template_by_value(ODD), "/api/template-store/templates/by-value/"),
    (lambda c: c.get_document(ODD), "/api/document-store/documents/"),
    (lambda c: c.get_document_versions(ODD), "/api/document-store/documents/"),
    (lambda c: c.get_file(ODD), "/api/document-store/files/"),
    # A key name may hold '#' and '?': raw, revoking "ci#2" revoked "ci".
    (lambda c: c.get_api_key(ODD), "/api/registry/api-keys/"),
    (lambda c: c.update_api_key(ODD, description="d"), "/api/registry/api-keys/"),
    (lambda c: c.revoke_api_key(ODD), "/api/registry/api-keys/"),
])
async def test_an_identifier_reaches_the_route_whole(recorded, call, route):
    client, paths = recorded
    await call(client)
    [path] = paths
    segment = path.split(route, 1)[1].split("/", 1)[0]
    assert unquote(segment) == ODD


def _unencoded_path_values(source: str, allowed: frozenset[str]) -> list[tuple[int, str]]:
    """Every value interpolated into the path of an f-string naming a WIP
    route (literal text holding '/api/'), other than one passed through
    quote(), seg() or _seg(), or an allowed expression. Adjacent literals are one
    f-string, so a URL split over lines is read whole, and a subscript or an
    attribute is checked like a bare name. The query part (after '?') is
    not a path."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.JoinedStr):
            continue
        in_path = False
        for part in node.values:
            if isinstance(part, ast.Constant):
                text = str(part.value)
                in_path = in_path or "/api/" in text
                if in_path and "?" in text.split("/api/")[-1]:
                    break
                continue
            if not in_path or not isinstance(part, ast.FormattedValue):
                continue
            expr = part.value
            func = expr.func if isinstance(expr, ast.Call) else None
            if func is not None and getattr(func, "id", getattr(func, "attr", None)) in ("quote", "seg", "_seg"):
                continue
            if ast.unparse(expr) not in allowed:
                found.append((node.lineno, ast.unparse(expr)))
    return found


# Namespace prefixes are one safe token by the Registry's rule; backup job
# and replay session ids are minted by the services.
_NOT_IDENTIFIERS = frozenset({"prefix", "namespace", "job_id", "session_id"})


def test_no_outgoing_url_interpolates_a_bare_identifier():
    offenders = [
        f"client.py:{lineno}: {{{expr}}}"
        for lineno, expr in _unencoded_path_values(
            Path(client_module.__file__).read_text(), _NOT_IDENTIFIERS,
        )
    ]
    assert offenders == [], "identifiers sent unencoded:\n" + "\n".join(offenders)
