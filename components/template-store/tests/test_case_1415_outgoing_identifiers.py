"""An identifier template-store sends on to another service is one URL path
segment, whatever characters the value holds.

def-store refuses only '/' in a terminology value, so 'C#' and 'Why?' are
storable. Interpolated raw, a template field referencing terminology 'C#'
was looked up as 'C' ('#' starts a fragment), and the template activated
against a terminology that does not exist. Every outgoing service URL
encodes the identifiers it carries; the scan below keeps it so.
"""

from __future__ import annotations

import ast
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

from template_store.services import def_store_client
from template_store.services.def_store_client import DefStoreClient


@pytest.fixture
def seen(monkeypatch):
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path.decode().split("?", 1)[0])
        return httpx.Response(404, json={"detail": "not found"})

    real = httpx.AsyncClient
    monkeypatch.setattr(def_store_client.httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    return paths


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["C#", "Why?", "50%"])
async def test_a_terminology_value_reaches_def_store_whole(seen, value):
    client = DefStoreClient(base_url="http://def-store", api_key="k")
    await client.get_terminology(terminology_value=value, namespace="ns")
    [path] = seen
    assert unquote(path.rsplit("/terminologies/by-value/", 1)[1]) == value


@pytest.mark.asyncio
async def test_a_terminology_id_reaches_def_store_whole(seen):
    client = DefStoreClient(base_url="http://def-store", api_key="k")
    await client.get_terminology(terminology_id="ns:C#?x")
    [path] = seen
    assert unquote(path.rsplit("/terminologies/", 1)[1]) == "ns:C#?x"


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


# Namespace prefixes are one safe token by the Registry's rule.
_NOT_IDENTIFIERS = frozenset({"namespace", "prefix"})


def test_no_outgoing_service_url_interpolates_a_bare_identifier():
    src = Path(def_store_client.__file__).parent
    offenders = [
        f"{path.name}:{lineno}: {{{expr}}}"
        for path in sorted(src.glob("*.py"))
        for lineno, expr in _unencoded_path_values(path.read_text(), _NOT_IDENTIFIERS)
    ]
    assert offenders == [], "identifiers sent unencoded:\n" + "\n".join(offenders)
