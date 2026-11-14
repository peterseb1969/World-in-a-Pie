"""Import bounds, table cells, refusals logged, existence signal, filters, paging.

Provenance: CASE-1146 (core slice).
"""

import csv
import io
import logging
import uuid

import httpx
import pytest

from document_store.api import import_api
from document_store.api.table_view import TableColumn, _flatten_document
from document_store.models.document import Document, DocumentStatus
from document_store.services import reporting_client
from document_store.services.reporting_client import ReportingSyncClient

API = "/api/document-store"
WIP_ONLY = {"X-API-Key": "test_xns_writer_key"}  # reads 'wip', not 'xns-closed'


def _csv(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


# --- import -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_refuses_an_upload_over_the_limit(client, auth_headers, monkeypatch):
    from document_store.main import settings

    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE", 100)
    body = _csv([{"national_id": f"1146{i:05d}", "first_name": "A" * 20, "last_name": "B"} for i in range(10)])
    for path, data in (
        ("import/preview", {}),
        ("import", {"template_id": "PERSON", "namespace": "wip",
                    "column_mapping": '{"national_id": "national_id"}'}),
    ):
        resp = await client.post(f"{API}/{path}", headers=auth_headers, data=data,
                                 files={"file": ("p.csv", body, "text/csv")})
        assert resp.status_code == 413, (path, resp.text)


@pytest.mark.asyncio
async def test_import_backs_off_like_every_write(client, auth_headers, monkeypatch):
    calls = []

    def delay():
        calls.append(1)
        return 0

    monkeypatch.setattr(import_api, "get_throttle_delay", delay)
    body = _csv([{"national_id": "114620001", "first_name": "Ada", "last_name": "Imp"}])
    resp = await client.post(
        f"{API}/import", headers=auth_headers,
        data={"template_id": "PERSON", "namespace": "wip", "column_mapping":
              '{"national_id": "national_id", "first_name": "first_name", "last_name": "last_name"}'},
        files={"file": ("p.csv", body, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["succeeded"] == 1
    assert calls


# --- table cells --------------------------------------------------------------------


def _row_doc(data: dict) -> Document:
    return Document(
        namespace="wip", document_id="D", template_id="T", template_version=1,
        identity_hash="h", version=1, data=data, status=DocumentStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_a_flattened_object_item_is_json_not_python_repr(client):
    column = TableColumn(name="items", label="items", type="array", is_array=True)
    rows, handling = _flatten_document(
        _row_doc({"items": [{"city": "Berlin"}]}), [column], ["items"], 1000,
    )
    assert handling == "flattened"
    assert rows[0]["items"] == '{"city": "Berlin"}'


@pytest.mark.asyncio
async def test_an_empty_array_kept_as_json_is_not_erased(client):
    column = TableColumn(name="items", label="items", type="array", is_array=True)
    rows, handling = _flatten_document(
        _row_doc({"items": [1, 2, 3]}), [column], ["items"], 1,
    )
    assert (handling, rows[0]["items"]) == ("json", "[1, 2, 3]")
    rows, handling = _flatten_document(_row_doc({"items": []}), [column], ["items"], 0)
    assert (handling, rows[0]["items"]) == ("json", "[]")


# --- reporting refusals are logged ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_reporting_refusal_is_logged(monkeypatch, caplog):
    transport = httpx.MockTransport(lambda request: httpx.Response(409, text="busy"))
    real = httpx.AsyncClient
    monkeypatch.setattr(
        reporting_client.httpx, "AsyncClient",
        lambda *a, **kw: real(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )
    caplog.set_level(logging.WARNING, logger=reporting_client.logger.name)
    client = ReportingSyncClient()
    assert await client.trigger_batch_sync("ns-x") is False
    assert await client.drop_namespace_schema("ns-x") is False
    messages = [r.getMessage() for r in caplog.records]
    assert any("batch sync for ns-x refused: HTTP 409" in m for m in messages), messages
    assert any("schema drop for ns-x refused: HTTP 409" in m for m in messages), messages


# --- existence signal ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_hidden_document_answers_as_a_missing_one(client, auth_headers):
    resp = await client.post(f"{API}/documents", headers=auth_headers, json=[{
        "namespace": "xns-closed", "template_id": "wip:PERSON",
        "data": {"national_id": "114630001", "first_name": "Hid", "last_name": "Den"},
    }])
    hidden = resp.json()["results"][0]["document_id"]
    missing = str(uuid.uuid4())
    for doc_id in (hidden, missing):
        r = await client.get(f"{API}/documents/{doc_id}", headers=WIP_ONLY)
        assert (r.status_code, r.json()["detail"]) == (
            404, f"Could not resolve document_id '{doc_id}' in namespace 'wip'",
        ), doc_id


# --- filters, pages, cursor ----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("flt", [
    {"field": "data.first_name", "operator": "in", "value": "Ada"},
    {"field": "data.first_name", "operator": "exists", "value": "yes"},
    {"field": "data.first_name", "operator": "regex", "value": "[unclosed"},
])
async def test_an_ill_formed_filter_operand_is_422(client, auth_headers, flt):
    resp = await client.post(f"{API}/documents/query", headers=auth_headers,
                             params={"namespace": "wip"}, json={"filters": [flt]})
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_an_empty_listing_has_zero_pages(client, auth_headers):
    resp = await client.get(f"{API}/documents", headers=auth_headers,
                            params={"namespace": "wip", "template_id": "PERSON"})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["total"], resp.json()["pages"]) == (0, 0)


@pytest.mark.asyncio
async def test_cursor_mode_starts_with_start(client, auth_headers):
    ids = []
    for i in range(3):
        resp = await client.post(f"{API}/documents", headers=auth_headers, json=[{
            "namespace": "wip", "template_id": "PERSON",
            "data": {"national_id": f"11464000{i}", "first_name": "Cur", "last_name": "Sor"},
        }])
        ids.append(resp.json()["results"][0]["document_id"])
    seen: list[str] = []
    cursor = "start"
    while cursor:
        resp = await client.get(f"{API}/documents", headers=auth_headers, params={
            "namespace": "wip", "template_id": "PERSON", "cursor": cursor, "page_size": 2,
        })
        assert resp.status_code == 200, resp.text
        seen += [d["document_id"] for d in resp.json()["items"]]
        cursor = resp.json()["next_cursor"]
    assert seen == ids
