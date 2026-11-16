"""An identifier document-store sends on to another service is one URL path
segment, whatever characters the value holds.

A term value has no character rule ('#', '?' and '%' are storable, '/' is
read through def-store's path routes), and documents name terms and
terminologies by value. Interpolated raw, an identifier is cut at '#' or
'?', or read across segments at '/', so def-store is asked about another
one. The scan keeps every outgoing service URL encoding what it carries.
"""

from __future__ import annotations

import ast
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


# Namespace prefixes are one safe token by the Registry's rule, and a
# deletion step's order is an int; neither is an entity identifier. Two
# messages name a backup job by its minted id.
_NOT_IDENTIFIERS = frozenset({
    "namespace", "prefix", "step_order", "running_job_id", "job.job_id",
})


def test_no_outgoing_service_url_interpolates_a_bare_identifier():
    src = Path(def_store_client.__file__).parent
    offenders = [
        f"{path.name}:{lineno}: {{{expr}}}"
        for path in sorted(src.glob("*.py"))
        for lineno, expr in _unencoded_path_values(path.read_text(), _NOT_IDENTIFIERS)
    ]
    assert offenders == [], "identifiers sent unencoded:\n" + "\n".join(offenders)
