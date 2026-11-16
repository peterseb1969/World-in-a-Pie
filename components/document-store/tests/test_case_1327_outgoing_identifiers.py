"""An identifier document-store sends on to another service is one URL path
segment, whatever characters the value holds.

A term value has no character rule ('#', '?' and '%' are storable, '/' is
read through def-store's path routes), and documents name terms and
terminologies by value. Interpolated raw, an identifier is cut at '#' or
'?', or read across segments at '/', so def-store is asked about another
one. The scan keeps every outgoing service URL encoding what it carries.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

from document_store.services import def_store_client
from document_store.services.def_store_client import DefStoreClient
from wip_auth.term_resolution import set_def_store_transport

VALUES = ["wip:LANGS:C#", "wip:LANGS:a?b", "wip:UNITS:mg/dL"]


@pytest.fixture
def seen():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path.decode())
        return httpx.Response(404, json={"detail": "not found"})

    set_def_store_transport(httpx.MockTransport(handler))
    yield paths
    set_def_store_transport(None)


def _sent(paths: list[str], route: str) -> str:
    [path] = paths
    segment = path.split(route, 1)[1].split("?", 1)[0]
    assert "#" not in segment and "/" not in segment
    return unquote(segment)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", VALUES)
async def test_get_term_sends_the_whole_value(seen, value):
    await DefStoreClient(base_url="http://def-store", api_key="k").get_term(value)
    assert _sent(seen, "/api/def-store/terms/") == value


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["wip:C#SHARP", "LANGS?x"])
async def test_get_terminology_sends_the_whole_value(seen, value):
    await DefStoreClient(base_url="http://def-store", api_key="k").get_terminology(value)
    assert _sent(seen, "/api/def-store/terminologies/") == value


# An f-string building another service's URL, and a bare interpolation in its
# path. Namespace prefixes are one safe token by the Registry's rule, and a
# deletion step's order is an int; neither is an entity identifier.
_SERVICE_URL = re.compile(r'f"\{[^}]*(?:_url|_base)\}/api/[a-z-]+/[^"]*"')
_BARE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")
_NOT_IDENTIFIERS = {"namespace", "prefix", "step_order"}


def test_no_outgoing_service_url_interpolates_a_bare_identifier():
    src = Path(def_store_client.__file__).parent
    offenders = []
    for path in sorted(src.glob("*.py")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            for literal in _SERVICE_URL.findall(line):
                route = literal.split("/api/", 1)[1].split("?", 1)[0]
                offenders += [
                    f"{path.name}:{lineno}: {{{m}}}"
                    for m in _BARE.findall(route) if m not in _NOT_IDENTIFIERS
                ]
    assert offenders == [], "identifiers sent unencoded:\n" + "\n".join(offenders)
