"""An identifier an MCP tool sends on to a WIP service is one URL path
segment, whatever characters the value holds.

Agents pass identifiers as they have them: a canonical id, a value, a
'ns:VALUE' form, a synonym. A term value has no character rule ('#', '?'
and '%' are storable), so neither do the value forms that can name one.
Interpolated raw, an identifier is cut at '#' or '?', or read across
segments at '/', and the service answers about another one. Namespace
prefixes, API key names, backup job ids and replay session ids are left as
they are: the Registry restricts prefixes and key names to safe tokens, and
the others are minted by the services.
"""

from __future__ import annotations

import re
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
])
async def test_an_identifier_reaches_the_route_whole(recorded, call, route):
    client, paths = recorded
    await call(client)
    [path] = paths
    segment = path.split(route, 1)[1].split("/", 1)[0]
    assert unquote(segment) == ODD


_URL = re.compile(r'f"(?:\{self\.[a-z_]+_url\})?/api/[a-z-]+/[^"]*"')
_BARE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")
_NOT_IDENTIFIERS = {"prefix", "namespace", "job_id", "session_id", "name"}


def test_no_outgoing_url_interpolates_a_bare_identifier():
    offenders = []
    for lineno, line in enumerate(Path(client_module.__file__).read_text().splitlines(), 1):
        for literal in _URL.findall(line):
            route = literal.split("/api/", 1)[1].split("?", 1)[0]
            offenders += [
                f"client.py:{lineno}: {{{m}}}"
                for m in _BARE.findall(route) if m not in _NOT_IDENTIFIERS
            ]
    assert offenders == [], "identifiers sent unencoded:\n" + "\n".join(offenders)
