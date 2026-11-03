"""A bulk write resolves its term values up front, in one def-store request.

Each document's term validation used to send def-store its own request for
the values the batch had not resolved yet, ten documents at a time, so a
500-row batch of term-heavy documents made over a hundred requests. The
batch's distinct values are now resolved before validation, inside the
write's term_resolution_batch, and every document is answered from there.
These tests drive the real resolver (wip_auth.term_resolution) against a
recording stand-in for def-store's /validate/bulk.
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from document_store.services import validation_service as vs
from document_store.services.def_store_client import DefStoreClient
from document_store.services.validation_service import ValidationResult, ValidationService
from wip_auth.term_resolution import set_def_store_transport, term_resolution_batch

TERMINOLOGY = "0190c000-0000-7000-0000-0000000000aa"
TEMPLATE = {
    "template_id": "TPL", "version": 1, "status": "active", "namespace": "wip",
    "identity_fields": [],
    "fields": [
        {"name": "colour", "type": "term", "terminology_ref": TERMINOLOGY},
        {"name": "tags", "type": "array", "array_item_type": "term",
         "array_terminology_ref": TERMINOLOGY},
    ],
}


class _DefStore:
    """Answers /validate/bulk: every value but 'bad' is a term."""

    def __init__(self, fail_first: bool = False):
        self.requests: list[int] = []
        self.fail_first = fail_first

    def __call__(self, request: httpx.Request) -> httpx.Response:
        items = json.loads(request.content)["items"]
        self.requests.append(len(items))
        if self.fail_first and len(self.requests) == 1:
            return httpx.Response(503, text="unavailable")
        results = [
            {"valid": False, "suggestion": None} if it["value"] == "bad" else
            {"valid": True, "matched_via": "value",
             "matched_term": {"term_id": f"T-{it['value']}", "value": it["value"]}}
            for it in items
        ]
        return httpx.Response(200, json={"results": results})


@pytest.fixture
def def_store():
    stub = _DefStore()
    set_def_store_transport(httpx.MockTransport(stub))
    try:
        yield stub
    finally:
        set_def_store_transport(None)


@pytest.fixture
def wired():
    """Validation reaches the real resolver and a fixed template."""
    templates = AsyncMock()
    templates.get_template_resolved = AsyncMock(return_value=TEMPLATE)
    with (
        patch.object(vs, "get_def_store_client",
                     return_value=DefStoreClient(base_url="http://def-store", api_key="k")),
        patch.object(vs, "get_template_store_client", return_value=templates),
    ):
        yield


def _docs(n: int) -> list[dict]:
    return [{"colour": f"c{i}", "tags": [f"t{i}", "shared"]} for i in range(n)]


async def _stage4(service: ValidationService, data: dict) -> ValidationResult:
    result = ValidationResult()
    await service._validate_terms(data, TEMPLATE, result, namespace="wip")
    return result


@pytest.mark.asyncio
async def test_a_prefetched_batch_sends_one_request(def_store, wired):
    service = ValidationService()
    docs = _docs(50)
    with term_resolution_batch():
        await service.prefetch_term_values([("TPL", 1, d) for d in docs], "wip")
        results = [await _stage4(service, d) for d in docs]
    assert def_store.requests == [101]  # 50 colours + 50 tags + "shared"
    assert all(r.valid for r in results)
    assert {t["term_id"] for t in results[0].term_references} == {"T-c0", "T-t0", "T-shared"}


@pytest.mark.asyncio
async def test_without_the_prefetch_each_document_asks(def_store, wired):
    service = ValidationService()
    with term_resolution_batch():
        for d in _docs(5):
            await _stage4(service, d)
    assert len(def_store.requests) == 5


@pytest.mark.asyncio
async def test_the_prefetch_is_chunked(def_store, wired, monkeypatch):
    monkeypatch.setattr(vs, "TERM_PREFETCH_CHUNK", 40)
    with term_resolution_batch():
        await ValidationService().prefetch_term_values([("TPL", 1, d) for d in _docs(50)], "wip")
    assert def_store.requests == [40, 40, 21]


@pytest.mark.asyncio
async def test_an_invalid_value_still_fails_its_document(def_store, wired):
    service = ValidationService()
    docs = [{"colour": "bad"}, {"colour": "red"}]
    with term_resolution_batch():
        await service.prefetch_term_values([("TPL", 1, d) for d in docs], "wip")
        bad, good = [await _stage4(service, d) for d in docs]
    assert not bad.valid and good.valid
    assert len(def_store.requests) == 1


@pytest.mark.asyncio
async def test_a_failed_prefetch_leaves_documents_to_resolve_their_own(wired):
    stub = _DefStore(fail_first=True)
    set_def_store_transport(httpx.MockTransport(stub))
    try:
        service = ValidationService()
        docs = _docs(3)
        with term_resolution_batch():
            await service.prefetch_term_values([("TPL", 1, d) for d in docs], "wip")
            results = [await _stage4(service, d) for d in docs]
    finally:
        set_def_store_transport(None)
    assert all(r.valid for r in results)
    assert len(stub.requests) == 1 + 3  # the failed prefetch, then each document


@pytest.mark.asyncio
async def test_bulk_timing_reports_the_prefetch(client, auth_headers):
    resp = await client.post("/api/document-store/documents", headers=auth_headers, json=[
        {"template_id": "PERSON", "namespace": "wip",
         "data": {"national_id": f"{uuid.uuid4().int % 10**9:09d}", "first_name": "A",
                  "last_name": "B", "gender": "M"}}
        for _ in range(2)
    ])
    body = resp.json()
    assert [r["status"] for r in body["results"]] == ["created", "created"], body
    assert "0t_term_prefetch" in body["timing"]
