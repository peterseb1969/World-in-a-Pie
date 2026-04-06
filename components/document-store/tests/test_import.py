"""Tests for CSV/XLSX document import."""

import io
import json
import pytest
import openpyxl
from httpx import AsyncClient


def _make_csv(headers: list[str], rows: list[list[str]]) -> bytes:
    """Build a CSV file in memory."""
    lines = [",".join(headers)]
    for row in rows:
        lines.append(",".join(row))
    return "\n".join(lines).encode("utf-8")


def _make_xlsx(headers: list[str], rows: list[list]) -> bytes:
    """Build an XLSX file in memory."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_preview_csv(client: AsyncClient, auth_headers: dict):
    """Preview a CSV file — returns headers and sample rows."""
    csv_bytes = _make_csv(
        ["national_id", "first_name", "last_name"],
        [
            ["123456789", "Alice", "Smith"],
            ["987654321", "Bob", "Jones"],
            ["111222333", "Carol", "Lee"],
        ],
    )
    response = await client.post(
        "/api/document-store/import/preview",
        headers=auth_headers,
        files={"file": ("test.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["format"] == "csv"
    assert data["headers"] == ["national_id", "first_name", "last_name"]
    assert data["total_rows"] == 3
    assert len(data["sample_rows"]) == 3


@pytest.mark.asyncio
async def test_preview_empty_file(client: AsyncClient, auth_headers: dict):
    """Preview an empty file returns error."""
    response = await client.post(
        "/api/document-store/import/preview",
        headers=auth_headers,
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_import_csv_success(client: AsyncClient, auth_headers: dict):
    """Import a CSV file with valid data creates documents."""
    csv_bytes = _make_csv(
        ["national_id", "first_name", "last_name"],
        [
            ["123456789", "Alice", "Smith"],
            ["987654321", "Bob", "Jones"],
        ],
    )
    mapping = json.dumps({
        "national_id": "national_id",
        "first_name": "first_name",
        "last_name": "last_name",
    })
    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data={
            "template_id": "PERSON",
            "column_mapping": mapping,
            "namespace": "wip",
            "skip_errors": "false",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_rows"] == 2
    assert data["succeeded"] == 2
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_import_csv_with_bad_rows_skip(client: AsyncClient, auth_headers: dict):
    """Import with skip_errors=true skips bad rows and imports good ones.

    The bad row is missing first_name (mandatory field) — it's mapped but empty,
    so build_documents skips it, triggering a 'missing required field' validation error.
    """
    csv_bytes = _make_csv(
        ["national_id", "first_name", "last_name"],
        [
            ["123456789", "Alice", "Smith"],   # good — all mandatory fields present
            ["987654321", "", "Jones"],         # bad — first_name is empty → omitted → missing mandatory
            ["111222333", "Carol", "Lee"],      # good
        ],
    )
    mapping = json.dumps({
        "national_id": "national_id",
        "first_name": "first_name",
        "last_name": "last_name",
    })
    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data={
            "template_id": "PERSON",
            "column_mapping": mapping,
            "namespace": "wip",
            "skip_errors": "true",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_rows"] == 3
    assert data["succeeded"] == 2
    assert data["failed"] == 1
    assert len(data["errors"]) == 1


@pytest.mark.asyncio
async def test_import_csv_without_skip_stops_on_error(client: AsyncClient, auth_headers: dict):
    """Import with skip_errors=false stops at first error.

    First row is missing first_name (mandatory), so import stops immediately.
    """
    csv_bytes = _make_csv(
        ["national_id", "first_name", "last_name"],
        [
            ["123456789", "", "Smith"],      # bad — missing mandatory first_name
            ["987654321", "Bob", "Jones"],    # never reached
            ["111222333", "Carol", "Lee"],    # never reached
        ],
    )
    mapping = json.dumps({
        "national_id": "national_id",
        "first_name": "first_name",
        "last_name": "last_name",
    })
    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data={
            "template_id": "PERSON",
            "column_mapping": mapping,
            "namespace": "wip",
            "skip_errors": "false",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["failed"] == 1
    assert data["skipped"] == 2  # remaining rows skipped


@pytest.mark.asyncio
async def test_import_invalid_column_mapping(client: AsyncClient, auth_headers: dict):
    """Import with invalid column mapping returns error."""
    csv_bytes = _make_csv(["col_a", "col_b"], [["1", "2"]])
    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data={
            "template_id": "PERSON",
            "column_mapping": json.dumps({"nonexistent_column": "first_name"}),
            "namespace": "wip",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert "nonexistent_column" in data["error"]


@pytest.mark.asyncio
async def test_import_bad_json_mapping(client: AsyncClient, auth_headers: dict):
    """Import with malformed JSON column_mapping returns error."""
    csv_bytes = _make_csv(["a"], [["1"]])
    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data={
            "template_id": "PERSON",
            "column_mapping": "not valid json{{{",
            "namespace": "wip",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert "JSON" in data["error"]


@pytest.mark.asyncio
async def test_preview_xlsx(client: AsyncClient, auth_headers: dict):
    """Preview an XLSX file — returns format, headers, and sample rows."""
    xlsx_bytes = _make_xlsx(
        ["national_id", "first_name", "last_name"],
        [
            ["123456789", "Alice", "Smith"],
            ["987654321", "Bob", "Jones"],
            ["111222333", "Carol", "Lee"],
        ],
    )
    response = await client.post(
        "/api/document-store/import/preview",
        headers=auth_headers,
        files={"file": ("test.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["format"] == "xlsx"
    assert data["headers"] == ["national_id", "first_name", "last_name"]
    assert data["total_rows"] == 3
    assert len(data["sample_rows"]) == 3
