"""The aggregator asks both stores at once, and one dead store cannot hide the
other's verdict.

Provenance: CASE-1278. `GET /health/integrity` called template-store and then
document-store, so its worst case was the sum of the two budgets (30 s + the
document scan's), and a def-store outage that made both stores slow turned a
health check into a two-and-a-half-minute request the toolkit gave up on. The
two asks now run together: the ceiling is the larger budget, not the sum.

The result does not depend on which answer arrives first: the services are
listed, and their issues folded, in a fixed order.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from reporting_sync import main
from reporting_sync.main import app

ADMIN = "rs_test_admin_key"
P = "/api/reporting-sync"

_TEMPLATE_ANSWER = {"status": "healthy", "summary": {"total_templates": 2}}
_DOCUMENT_ANSWER = {
    "status": "healthy",
    "summary": {"total_documents": 5, "documents_checked": 5},
}


async def _call() -> httpx.Response:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test",
                          headers={"X-API-Key": ADMIN}) as c:
        return await c.request("GET", f"{P}/health/integrity")


def _name_of(request: httpx.Request) -> str:
    return ("template-store" if str(request.url).startswith(main.settings.template_store_url)
            else "document-store")


def _patch_transport(monkeypatch, handler) -> None:
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", client)


@pytest.mark.asyncio
async def test_both_stores_are_asked_while_the_other_is_still_answering(monkeypatch):
    """The ask that arrives first is held open: the other must start inside it,
    which it cannot when the stores are queried one after the other."""
    started: dict[str, float] = {}
    finished: dict[str, float] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        name = _name_of(request)
        started[name] = time.monotonic()
        if len(started) == 1:
            await asyncio.sleep(0.3)  # hold the first answer back
        finished[name] = time.monotonic()
        answer = _TEMPLATE_ANSWER if name == "template-store" else _DOCUMENT_ANSWER
        return httpx.Response(200, json=answer)

    _patch_transport(monkeypatch, handler)
    body = (await _call()).json()

    assert sorted(started) == ["document-store", "template-store"]
    assert started["document-store"] < finished["template-store"], "asked one after the other"
    assert started["template-store"] < finished["document-store"], "asked one after the other"
    assert body["services_checked"] == ["template-store", "document-store"]
    assert body["status"] == "healthy", body


@pytest.mark.asyncio
async def test_the_result_reads_the_same_whichever_answer_arrives_first(monkeypatch):
    """Slow template-store, instant document-store: the lists and the issues keep
    the fixed order, so a run is comparable with the next."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if _name_of(request) == "template-store":
            await asyncio.sleep(0.2)
            return httpx.Response(200, json={
                "status": "warning",
                "summary": {"total_templates": 2, "inactive_terminology_refs": 1},
                "issues": [{"type": "inactive_terminology_ref", "severity": "warning",
                           "template_id": "T-1", "template_value": "A",
                           "template_version": 1, "reference": "GENDER", "message": "gone"}],
            })
        return httpx.Response(200, json={
            "status": "error",
            "summary": {"total_documents": 5, "documents_checked": 5, "orphaned_term_refs": 1},
            "issues": [{"type": "orphaned_term_ref", "severity": "error",
                       "document_id": "D-1", "field_path": "gender",
                       "reference": "TERM-1", "message": "missing"}],
        })

    _patch_transport(monkeypatch, handler)
    body = (await _call()).json()

    assert [(i["source"], i["entity_id"]) for i in body["issues"]] == [
        ("template-store", "T-1"), ("document-store", "D-1"),
    ]
    assert body["status"] == "error", body
    assert body["summary"]["inactive_refs"] == 1
    assert body["summary"]["orphaned_term_refs"] == 1


@pytest.mark.asyncio
async def test_a_store_that_dies_does_not_cancel_the_other(monkeypatch):
    """An unreachable store is named as unavailable; the store that answered
    still contributes its counts and issues."""
    async def handler(request: httpx.Request) -> httpx.Response:
        if _name_of(request) == "template-store":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={
            "status": "incomplete",
            "summary": {"total_documents": 5, "documents_checked": 5,
                       "documents_with_issues": 0},
            "issues": [],
        })

    _patch_transport(monkeypatch, handler)
    body = (await _call()).json()

    assert body["services_unavailable"] == ["template-store"]
    assert body["services_checked"] == ["document-store"]
    assert body["services_incomplete"] == ["document-store"]
    assert body["status"] == "partial", body


@pytest.mark.asyncio
async def test_the_worst_case_is_the_larger_budget_not_the_sum(monkeypatch):
    """Both stores at their slowest: together they finish inside one budget."""
    slow = 0.6

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(slow)
        name = _name_of(request)
        return httpx.Response(200, json=_TEMPLATE_ANSWER if name == "template-store"
                             else _DOCUMENT_ANSWER)

    _patch_transport(monkeypatch, handler)
    began = time.monotonic()
    body = (await _call()).json()
    elapsed = time.monotonic() - began

    assert body["status"] == "healthy", body
    assert elapsed < 2 * slow, f"the two asks ran one after the other ({elapsed:.2f}s)"


@pytest.mark.asyncio
async def test_a_200_without_a_json_body_is_no_verdict_not_a_500(monkeypatch):
    """An unrouted path through Caddy answers 200 with an empty body. That is
    a store giving no verdict, as before the asks ran together: the
    aggregate names it unavailable and still reports the other store."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if _name_of(request) == "template-store":
            return httpx.Response(200, content=b"")
        return httpx.Response(200, json=_DOCUMENT_ANSWER)

    _patch_transport(monkeypatch, handler)
    response = await _call()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["services_unavailable"] == ["template-store"]
    assert body["services_checked"] == ["document-store"]
