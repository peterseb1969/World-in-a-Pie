"""An identifier reporting-sync sends on to another service is one URL path
segment, whatever characters the value holds.

A term value has no character rule ('#', '?', '%' are storable, and '/' is
read through def-store's path routes), and the platform addresses terms by
value. An identifier interpolated raw into an outgoing URL is cut at '#'
(a fragment) or '?' (a query), or read across segments at '/', so the
target service is asked about another identifier. Every outgoing service
URL encodes the identifiers it carries; the scan below keeps it so.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from reporting_sync import search_service
from reporting_sync.search_service import SearchService

VALUES = ["wip:LANGS:C#", "wip:LANGS:a?b", "wip:UNITS:mg/dL", "wip:PCT:50%"]


@pytest.mark.asyncio
@pytest.mark.parametrize("term_id", VALUES)
async def test_the_term_references_door_asks_def_store_for_the_whole_value(monkeypatch, term_id):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(404, json={"detail": "not found"})

    real = httpx.AsyncClient
    monkeypatch.setattr(search_service.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))

    await SearchService.__new__(SearchService)._get_term_references(term_id)

    assert len(seen) == 1
    sent = seen[0].rsplit("/api/def-store/terms/", 1)[1]
    assert "#" not in sent and "?" not in sent and "/" not in sent
    from urllib.parse import unquote
    assert unquote(sent) == term_id


# An f-string building another service's URL: "{<something>_url}/api/<svc>/..."
_SERVICE_URL = re.compile(r'f"\{[^}]*_url\}/api/[a-z-]+/[^"]*"')
# A bare interpolation in the path part (before any '?'): {name} or {a.b}
_BARE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")


def test_no_outgoing_service_url_interpolates_a_bare_identifier():
    src = Path(search_service.__file__).parent
    offenders = []
    for path in sorted(src.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for literal in _SERVICE_URL.findall(line):
                route = literal.split("/api/", 1)[1].split("?", 1)[0]
                offenders += [f"{path.name}:{lineno}: {{{m}}}" for m in _BARE.findall(route)]
    assert offenders == [], "identifiers sent unencoded:\n" + "\n".join(offenders)
