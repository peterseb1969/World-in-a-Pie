"""Bulk writes report every item, stop where asked, and stay within their bounds.

- DELETE and PATCH /documents report a namespace refusal on its item at HTTP
  200 (``forbidden`` when the namespace is readable, ``not_found`` worded as a
  missing document when it is not), and the rest of the batch proceeds.
  DELETE checked permission inside its loop with a call that raises, so one
  unwritable item aborted the request after earlier deletes had landed;
  PATCH refused the whole request with a 403.
- A bulk create with continue_on_error=false stops at the first item the
  reference gates (stage 1b) refuse, as stages 1 and 4 stop.
- validate-candidate caps its explicit documents like every bulk surface.
- An integrity check with a limit reads and checks no more than the limit.
- A batch sync that started no job is not reported as accepted.
"""

import logging
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient

from document_store.models.api_models import MAX_BULK_ITEMS
from document_store.models.document import Document, DocumentStatus
from document_store.services import integrity_service, reporting_client
from document_store.services.reference_validator import ReferenceValidationError
from document_store.services.reporting_client import ReportingSyncClient

URL = "/api/document-store/documents"
READER = {"X-API-Key": "test_xns_reader_key"}  # write on wip, read on xns-closed
WRITER = {"X-API-Key": "test_xns_writer_key"}  # wip only: xns-closed is invisible
MISSING = "019a0000-0000-7000-8000-000000000000"


def _person(national_id: str, **data) -> dict:
    return {
        "namespace": "wip", "template_id": "PERSON",
        "data": {"national_id": national_id, "first_name": "John",
                 "last_name": "Doe", "age": 34, **data},
    }


async def _create(client, headers, *national_ids) -> list[str]:
    resp = await client.post(URL, headers=headers, json=[_person(n) for n in national_ids])
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert all(r["status"] == "created" for r in results), results
    return [r["document_id"] for r in results]


async def _closed_row() -> str:
    """An active document in xns-closed, written directly."""
    doc_id = str(uuid.uuid4())
    await Document(
        namespace="xns-closed", document_id=doc_id, template_id=str(uuid.uuid4()),
        template_version=1, template_value="T1140", identity_hash=f"h-{doc_id}",
        version=1, data={"x": 1}, status=DocumentStatus.ACTIVE,
    ).insert()
    return doc_id


async def _status(doc_id: str) -> DocumentStatus:
    rows = await Document.find({"document_id": doc_id}).to_list()
    return max(rows, key=lambda r: r.version).status


# --- item 1: per-item refusals --------------------------------------------


@pytest.mark.asyncio
async def test_delete_mixed_batch_reports_forbidden_item(client: AsyncClient, auth_headers):
    first, last = await _create(client, auth_headers, "114000001", "114000002")
    closed = await _closed_row()
    resp = await client.request("DELETE", URL, headers=READER, json=[
        {"id": first}, {"id": closed}, {"id": last},
    ])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["status"] for r in body["results"]] == ["deleted", "error", "deleted"]
    assert body["results"][1]["error_code"] == "forbidden"
    assert (body["succeeded"], body["failed"]) == (2, 1)
    assert await _status(first) == DocumentStatus.INACTIVE
    assert await _status(closed) == DocumentStatus.ACTIVE
    assert await _status(last) == DocumentStatus.INACTIVE


@pytest.mark.asyncio
async def test_patch_mixed_batch_reports_forbidden_item(client: AsyncClient, auth_headers):
    first, last = await _create(client, auth_headers, "114000011", "114000012")
    closed = await _closed_row()
    resp = await client.patch(URL, headers=READER, json=[
        {"document_id": d, "patch": {"first_name": "Jane"}} for d in (first, closed, last)
    ])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["index"] for r in body["results"]] == [0, 1, 2]
    assert [r["status"] for r in body["results"]] == ["updated", "error", "updated"]
    assert body["results"][1]["error_code"] == "forbidden"
    assert body["results"][1]["document_id"] == closed
    assert [r["document_id"] for r in body["results"]] == [first, closed, last]
    assert (body["succeeded"], body["failed"]) == (2, 1)
    row = await Document.find_one({"document_id": closed})
    assert (row.version, row.data) == (1, {"x": 1})


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["DELETE", "PATCH"])
async def test_invisible_namespace_answers_like_a_missing_document(
    client: AsyncClient, auth_headers, verb,
):
    [mine] = await _create(client, auth_headers, f"11400002{len(verb)}")
    closed = await _closed_row()
    if verb == "DELETE":
        items = [{"id": d} for d in (mine, closed, MISSING)]
        resp = await client.request("DELETE", URL, headers=WRITER, json=items)
    else:
        items = [{"document_id": d, "patch": {"first_name": "Jane"}} for d in (mine, closed, MISSING)]
        resp = await client.patch(URL, headers=WRITER, json=items)
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert results[0]["status"] in ("deleted", "updated"), results
    hidden, absent = results[1], results[2]
    assert (hidden["error_code"], hidden["error"]) == (absent["error_code"], absent["error"])
    assert hidden["error_code"] == "not_found"
    assert await _status(closed) == DocumentStatus.ACTIVE


@pytest.mark.asyncio
async def test_authorization_outage_refuses_delete_before_any_write(
    client: AsyncClient, auth_headers,
):
    [mine] = await _create(client, auth_headers, "114000031")
    closed = await _closed_row()

    async def answer(identity, namespace):
        return "unavailable" if namespace == "xns-closed" else "write"

    with patch("wip_auth.permissions.resolve_permission", side_effect=answer):
        resp = await client.request("DELETE", URL, headers=READER, json=[
            {"id": mine}, {"id": closed},
        ])
    assert resp.status_code == 503, resp.text
    assert await _status(mine) == DocumentStatus.ACTIVE


# --- item 2: stage 1b honours continue_on_error=false -----------------------


@pytest.mark.asyncio
async def test_stage_1b_refusal_stops_the_batch(client: AsyncClient, auth_headers):
    calls = 0

    async def gate(self, namespace, template_id, vr, placements=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ReferenceValidationError("refused", [{"reason": "test"}])

    ids = ["114000041", "114000042", "114000043"]
    with patch(
        "document_store.services.document_service.DocumentService._check_new_references",
        gate,
    ):
        resp = await client.post(
            f"{URL}?continue_on_error=false", headers=auth_headers,
            json=[_person(n) for n in ids],
        )
    assert resp.status_code == 200, resp.text
    results = sorted(resp.json()["results"], key=lambda r: r["index"])
    assert [r["status"] for r in results] == ["created", "error", "skipped"], results
    written = await Document.find({"data.national_id": {"$in": ids}}).to_list()
    assert [d.data["national_id"] for d in written] == ["114000041"]


# --- item 6: validate-candidate cap -----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("count, status", [(MAX_BULK_ITEMS, 200), (MAX_BULK_ITEMS + 1, 422)])
async def test_validate_candidate_caps_documents(client: AsyncClient, auth_headers, count, status):
    resp = await client.post(
        "/api/document-store/validation/validate-candidate", headers=auth_headers,
        json={"namespace": "wip",
              "template_definition": {"value": "C1140", "label": "C", "fields": []},
              "documents": [{}] * count},
    )
    assert resp.status_code == status, resp.text[:300]


# --- item 8: integrity limit bounds the scan --------------------------------


@pytest.mark.asyncio
async def test_integrity_limit_bounds_what_is_checked(client: AsyncClient):
    ns = "test-ns"
    await Document.insert_many([
        Document(
            namespace=ns, document_id=str(uuid.uuid4()), template_id="t", template_version=1,
            template_value="T", identity_hash=f"h{i}", version=1, data={},
            status=DocumentStatus.ACTIVE,
        )
        for i in range(integrity_service.BATCH_SIZE + 1)
    ])
    checked = AsyncMock()
    with (
        patch.object(integrity_service, "check_template_reference", checked),
        patch.object(integrity_service, "check_batch_references", AsyncMock()),
    ):
        result = await integrity_service.check_all_documents(
            namespace=ns, limit=10, check_term_refs=False, check_identity=False,
        )
    assert result.summary.documents_checked == 10
    assert checked.await_count == 10


# --- item 7: a batch sync that started nothing ------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("jobs, queued", [
    ([], False),
    ([{"template_value": "A", "status": "failed", "message": "boom"}], False),
    ([{"template_value": "A", "status": "pending", "message": ""},
      {"template_value": "B", "status": "failed", "message": "boom"}], True),
])
async def test_batch_sync_reports_whether_a_job_was_queued(monkeypatch, caplog, jobs, queued):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=jobs))
    real = httpx.AsyncClient
    monkeypatch.setattr(
        reporting_client.httpx, "AsyncClient",
        lambda *a, **kw: real(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )
    caplog.set_level(logging.WARNING, logger=reporting_client.logger.name)
    assert await ReportingSyncClient().trigger_batch_sync("ns-x") is queued
    if any(j["status"] == "failed" for j in jobs):
        assert any("did not start" in r.getMessage() for r in caplog.records)
