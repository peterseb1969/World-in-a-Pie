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

import ast
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


def test_no_outgoing_service_url_interpolates_a_bare_identifier():
    src = Path(search_service.__file__).parent
    offenders = [
        f"{path.name}:{lineno}: {{{expr}}}"
        for path in sorted(src.glob("*.py"))
        for lineno, expr in _unencoded_path_values(path.read_text(), frozenset())
    ]
    assert offenders == [], "identifiers sent unencoded:\n" + "\n".join(offenders)


# Template ids are caller-assignable on restore and migration, with no
# character rule. The parity sweep sent two of them raw (CASE-1415).
ODD_TEMPLATE = "T#1?a"


@pytest.mark.asyncio
async def test_parity_rereads_an_extending_template_by_its_whole_id(monkeypatch):
    from reporting_sync import parity

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode().split("?", 1)[0])
        if request.url.path.endswith("/templates"):
            return httpx.Response(200, json={"items": [
                {"template_id": ODD_TEMPLATE, "extends": "P", "version": 1},
            ]})
        return httpx.Response(200, json={"template_id": ODD_TEMPLATE})

    real = httpx.AsyncClient
    monkeypatch.setattr(parity.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))

    await parity._fetch_namespace_templates("ns")

    from urllib.parse import unquote
    [reread] = [p for p in seen if not p.endswith("/templates")]
    assert unquote(reread.rsplit("/templates/", 1)[1]) == ODD_TEMPLATE


@pytest.mark.asyncio
async def test_parity_reads_a_foreign_template_by_its_whole_id():
    from reporting_sync import parity

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"template_id": ODD_TEMPLATE, "namespace": "other"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        item = await parity._read_foreign_template(client, {"template_id": ODD_TEMPLATE}, [])

    from urllib.parse import unquote
    assert item is not None
    [path] = seen
    assert unquote(path.rsplit("/templates/", 1)[1]) == ODD_TEMPLATE
