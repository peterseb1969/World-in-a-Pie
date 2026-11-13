"""File import converts each cell to its field's type before validation: a
numeric or date column imports, and a cell its type cannot read fails its
row, never the file."""

import csv
import io
import json
from datetime import datetime

import openpyxl
import pytest
from httpx import AsyncClient

from document_store.models.document import Document
from document_store.services.import_service import CellConversionError, ImportService, convert_cell


def _csv(headers, rows) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _xlsx(headers, rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


MAPPING = {c: c for c in ("national_id", "first_name", "last_name", "age", "birth_date")}
HEADERS = list(MAPPING)


async def _import(client, auth_headers, filename, content, skip_errors=True):
    resp = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": (filename, content, "application/octet-stream")},
        data={"template_id": "PERSON", "column_mapping": json.dumps(MAPPING),
              "namespace": "wip", "skip_errors": str(skip_errors).lower()},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _stored(result) -> Document:
    return await Document.find_one({"document_id": result["document_id"]})


# ---------------------------------------------------------------------------
# Through the endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_csv_with_integer_and_date_columns_imports(client: AsyncClient, auth_headers):
    body = await _import(client, auth_headers, "people.csv", _csv(HEADERS, [
        ["111000111", "Ada", "Lovelace", "36", "1815-12-10"],
        ["111000222", "Alan", "Turing", " 41 ", "1912-06-23"],
    ]))
    assert body["succeeded"] == 2 and body["failed"] == 0, body
    first = await _stored(body["results"][0])
    assert first.data["age"] == 36 and isinstance(first.data["age"], int)
    assert first.data["birth_date"] == "1815-12-10"
    assert first.data["national_id"] == "111000111"  # a string field keeps its text


@pytest.mark.asyncio
async def test_an_xlsx_with_number_and_date_cells_imports(client: AsyncClient, auth_headers):
    body = await _import(client, auth_headers, "people.xlsx", _xlsx(HEADERS, [
        [111000333, "Grace", "Hopper", 85, datetime(1906, 12, 9)],
    ]))
    assert body["succeeded"] == 1 and body["failed"] == 0, body
    doc = await _stored(body["results"][0])
    assert doc.data["age"] == 85
    assert doc.data["birth_date"] == "1906-12-09"
    assert doc.data["national_id"] == "111000333"


@pytest.mark.asyncio
async def test_a_cell_its_type_cannot_read_fails_only_its_row(client: AsyncClient, auth_headers):
    body = await _import(client, auth_headers, "mixed.csv", _csv(HEADERS, [
        ["111000444", "Edsger", "Dijkstra", "seventy-two", "1930-05-11"],
        ["111000555", "Barbara", "Liskov", "86", "1939-11-07"],
    ]))
    assert body["succeeded"] == 1 and body["failed"] == 1, body
    [error] = body["errors"]
    assert error["row"] == 2
    assert "age (column 'age')" in error["error"] and "not an integer" in error["error"]


@pytest.mark.asyncio
async def test_preview_counts_every_row_but_shows_five(client: AsyncClient, auth_headers):
    rows = [[f"1110{n:05d}", "N", "N", str(n), "2000-01-01"] for n in range(12)]
    for name, content in (("p.csv", _csv(HEADERS, rows)), ("p.xlsx", _xlsx(HEADERS, rows))):
        resp = await client.post(
            "/api/document-store/import/preview", headers=auth_headers,
            files={"file": (name, content, "application/octet-stream")},
        )
        data = resp.json()
        assert data["total_rows"] == 12 and len(data["sample_rows"]) == 5, (name, data)


# ---------------------------------------------------------------------------
# convert_cell
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("value", "field_type", "expected"), [
    ("30", "integer", 30), (" 7 ", "integer", 7), (30, "integer", 30), (30.0, "integer", 30),
    ("0", "integer", 0),
    ("2.5", "number", 2.5), ("4", "number", 4), (99.5, "number", 99.5), ("1e3", "number", 1000.0),
    ("TRUE", "boolean", True), ("no", "boolean", False), ("1", "boolean", True),
    (False, "boolean", False), (0, "boolean", False),
    ("1990-04-12", "date", "1990-04-12"), (datetime(1990, 4, 12), "date", "1990-04-12"),
    (datetime(1990, 4, 12, 8, 30), "datetime", "1990-04-12T08:30:00"),
    ("1990-04-12T08:30:00", "datetime", "1990-04-12T08:30:00"),
    (" 12/04/1990 08:30 ", "datetime", "12/04/1990 08:30"),  # text as written, for validation to judge
    (datetime(1990, 4, 12), "string", "1990-04-12T00:00:00"),
    ("+7", "integer", 7), ("-3", "integer", -3), (".5", "number", 0.5), ("-2E2", "number", -200.0),
    ("123456789", "string", "123456789"), (123456789, "string", "123456789"),
    (12.0, "string", "12"), ("United Kingdom", "term", "United Kingdom"),
    ("x", None, "x"),
])
def test_a_cell_becomes_its_fields_type(value, field_type, expected):
    converted = convert_cell(value, field_type)
    assert converted == expected and type(converted) is type(expected)


@pytest.mark.parametrize(("value", "field_type"), [
    ("thirty", "integer"), ("2.5", "integer"), (2.5, "integer"), (True, "integer"),
    ("abc", "number"), ("nan", "number"), ("inf", "number"), (True, "number"),
    ("1_000", "integer"), ("1_000", "number"), ("\u0661\u0662\u0663", "integer"),
    ("\u0661\u0662\u0663", "number"), ("1.2.3", "number"), ("", "integer"),
    ("maybe", "boolean"), (2, "boolean"),
    (datetime(1990, 4, 12, 8, 30), "date"),
])
def test_a_cell_its_type_cannot_read_is_refused(value, field_type):
    with pytest.raises(CellConversionError):
        convert_cell(value, field_type)


def test_a_blank_cell_is_not_provided():
    docs = ImportService.build_documents(
        [{"a": "", "b": None, "c": 0}], "T", {"a": "age", "b": "x", "c": "flag"}, "wip",
        field_types={"age": "integer", "flag": "boolean"},
    )
    assert docs[0]["data"] == {"flag": False}
    assert "conversion_errors" not in docs[0]


# ---------------------------------------------------------------------------
# The template fetch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "status", "code"), [
    (dict(unavailable=True), 503, "dependency_unavailable"),
    (dict(code="inheritance_unresolved"), 422, "inheritance_unresolved"),
    (dict(), 422, "template_error"),
])
async def test_a_template_store_failure_is_an_answer_not_a_500(client, auth_headers, error, status, code):
    """The import reads the template's field types before any row: a store
    that cannot answer is an outage, a template it cannot serve makes the
    file unusable, and neither is a bare 500."""
    from unittest.mock import AsyncMock, patch

    from document_store.services.template_store_client import TemplateStoreError

    store = AsyncMock()
    store.get_template.side_effect = TemplateStoreError("no", **error)
    with patch("document_store.api.import_api.get_template_store_client", return_value=store):
        resp = await client.post(
            "/api/document-store/import", headers=auth_headers,
            data={"template_id": "PERSON", "namespace": "wip",
                  "column_mapping": json.dumps({h: h for h in HEADERS})},
            files={"file": ("p.csv", _csv(HEADERS, [["111000001", "A", "B", "M", "2000-01-01"]]),
                            "application/octet-stream")},
        )
    assert resp.status_code == status, resp.text
    assert code in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [2**63, -(2**63) - 1])
async def test_a_whole_number_mongodb_cannot_hold_is_refused_at_validation(client, auth_headers, age):
    """MongoDB holds a 64-bit integer at most. A larger one used to pass
    validation and fail at storage, after the Registry had allocated the
    document's id; it is refused with the field's error instead, on the JSON
    API as on import."""
    resp = await client.post("/api/document-store/documents", headers=auth_headers, json=[{
        "template_id": "PERSON", "namespace": "wip",
        "data": {"national_id": "111222333", "first_name": "A", "last_name": "B",
                 "gender": "M", "age": age},
    }])
    assert resp.status_code == 200, resp.text
    [item] = resp.json()["results"]
    assert item["status"] == "error" and item["error_code"] == "validation_failed", item
    assert "2^63" in item["error"]
