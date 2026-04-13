"""Extended tests for CSV/XLSX import edge cases and validation."""

import io
import json
import pytest
import openpyxl
from httpx import AsyncClient


def _make_csv(headers: list[str], rows: list[list[str]], delimiter: str = ",") -> bytes:
    """Build a CSV file in memory with configurable delimiter."""
    lines = [delimiter.join(headers)]
    for row in rows:
        lines.append(delimiter.join(row))
    return "\n".join(lines).encode("utf-8")


def _make_csv_raw(text: str) -> bytes:
    """Build a CSV file from raw text (for testing quoted fields, empty rows, etc.)."""
    return text.encode("utf-8")


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


def _import_data(template_id: str, mapping: dict, namespace: str = "wip", skip_errors: str = "false") -> dict:
    """Build the form data dict for the import endpoint."""
    return {
        "template_id": template_id,
        "column_mapping": json.dumps(mapping),
        "namespace": namespace,
        "skip_errors": skip_errors,
    }


# ============================================================================
# CSV Import Edge Cases
# ============================================================================


@pytest.mark.asyncio
async def test_csv_quoted_fields_with_commas(client: AsyncClient, auth_headers: dict):
    """CSV with quoted fields containing commas parses values correctly."""
    csv_text = (
        'national_id,first_name,last_name\r\n'
        '123456789,"Smith, Jr.",Doe\r\n'
        '987654321,"O\'Brien","Lee, III"\r\n'
    )
    csv_bytes = _make_csv_raw(csv_text)
    mapping = {"national_id": "national_id", "first_name": "first_name", "last_name": "last_name"}

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data=_import_data("PERSON", mapping, skip_errors="true"),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_rows"] == 2
    assert data["succeeded"] == 2
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_csv_quoted_fields_preview(client: AsyncClient, auth_headers: dict):
    """Preview of CSV with quoted fields containing commas returns correct headers and data."""
    csv_text = (
        'national_id,first_name,last_name\r\n'
        '123456789,"Smith, Jr.",Doe\r\n'
    )
    csv_bytes = _make_csv_raw(csv_text)

    response = await client.post(
        "/api/document-store/import/preview",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["headers"] == ["national_id", "first_name", "last_name"]
    assert data["total_rows"] == 1
    # The quoted comma should be part of the value, not a delimiter
    assert data["sample_rows"][0]["first_name"] == "Smith, Jr."


@pytest.mark.asyncio
async def test_csv_empty_rows_skipped(client: AsyncClient, auth_headers: dict):
    """CSV with empty rows between data rows — empty rows produce empty values
    which are skipped in build_documents (no data), so only non-empty rows import."""
    csv_text = (
        'national_id,first_name,last_name\r\n'
        '123456789,Alice,Smith\r\n'
        ',,\r\n'
        '987654321,Bob,Jones\r\n'
    )
    csv_bytes = _make_csv_raw(csv_text)
    mapping = {"national_id": "national_id", "first_name": "first_name", "last_name": "last_name"}

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data=_import_data("PERSON", mapping, skip_errors="true"),
    )
    assert response.status_code == 200
    data = response.json()
    # 3 rows parsed total (including the empty one)
    assert data["total_rows"] == 3
    # The empty row will have no data → validation will fail (missing mandatory fields)
    # 2 good rows should succeed
    assert data["succeeded"] == 2
    assert data["failed"] == 1


@pytest.mark.asyncio
async def test_csv_extra_columns_ignored(client: AsyncClient, auth_headers: dict):
    """CSV with extra columns not in the mapping — they are simply ignored."""
    csv_bytes = _make_csv(
        ["national_id", "first_name", "last_name", "extra_col", "another_extra"],
        [
            ["123456789", "Alice", "Smith", "ignored_val", "also_ignored"],
            ["987654321", "Bob", "Jones", "foo", "bar"],
        ],
    )
    mapping = {"national_id": "national_id", "first_name": "first_name", "last_name": "last_name"}

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data=_import_data("PERSON", mapping),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_rows"] == 2
    assert data["succeeded"] == 2
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_csv_missing_mapped_column(client: AsyncClient, auth_headers: dict):
    """CSV where the mapping references a column that doesn't exist in the file."""
    csv_bytes = _make_csv(
        ["national_id", "first_name"],
        [["123456789", "Alice"]],
    )
    # Map a column that doesn't exist in the CSV
    mapping = {"national_id": "national_id", "first_name": "first_name", "missing_col": "last_name"}

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data=_import_data("PERSON", mapping),
    )
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert "missing_col" in data["error"]


@pytest.mark.asyncio
async def test_csv_utf8_special_characters(client: AsyncClient, auth_headers: dict):
    """CSV with UTF-8 special characters (accents, CJK) — preserved in import."""
    csv_text = (
        'national_id,first_name,last_name\r\n'
        '123456789,\u00c9lodie,Br\u00fcn\r\n'
        '987654321,\u592a\u90ce,\u5c71\u7530\r\n'
    )
    csv_bytes = _make_csv_raw(csv_text)
    mapping = {"national_id": "national_id", "first_name": "first_name", "last_name": "last_name"}

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data=_import_data("PERSON", mapping, skip_errors="true"),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_rows"] == 2
    assert data["succeeded"] == 2
    assert data["failed"] == 0

    # Verify the data was preserved by listing documents
    list_response = await client.get(
        "/api/document-store/documents",
        headers=auth_headers,
        params={"template_id": "PERSON", "page_size": 10},
    )
    assert list_response.status_code == 200
    docs = list_response.json()["items"]
    first_names = {d["data"]["first_name"] for d in docs}
    assert "\u00c9lodie" in first_names
    assert "\u592a\u90ce" in first_names


@pytest.mark.asyncio
async def test_csv_preview_returns_headers_and_samples(client: AsyncClient, auth_headers: dict):
    """Preview endpoint returns correct column headers and capped sample rows."""
    csv_bytes = _make_csv(
        ["national_id", "first_name", "last_name"],
        [
            ["111111111", "R1", "S1"],
            ["222222222", "R2", "S2"],
            ["333333333", "R3", "S3"],
            ["444444444", "R4", "S4"],
            ["555555555", "R5", "S5"],
            ["666666666", "R6", "S6"],
            ["777777777", "R7", "S7"],
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
    assert data["total_rows"] == 7
    # Preview caps at 5 sample rows
    assert len(data["sample_rows"]) == 5


# ============================================================================
# Import Validation
# ============================================================================


@pytest.mark.asyncio
async def test_import_invalid_term_value(client: AsyncClient, auth_headers: dict):
    """Import with invalid term value reports per-row error.

    The gender field is a term field referencing GENDER terminology.
    Valid values are M, F, O. 'INVALID' should fail validation.
    """
    csv_bytes = _make_csv(
        ["national_id", "first_name", "last_name", "gender"],
        [
            ["123456789", "Alice", "Smith", "INVALID"],
        ],
    )
    mapping = {
        "national_id": "national_id",
        "first_name": "first_name",
        "last_name": "last_name",
        "gender": "gender",
    }

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data=_import_data("PERSON", mapping, skip_errors="true"),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["failed"] == 1
    assert len(data["errors"]) == 1
    assert data["errors"][0]["row"] == 2  # row 2 (1-indexed + header)


@pytest.mark.asyncio
async def test_import_missing_required_field(client: AsyncClient, auth_headers: dict):
    """Import with missing required field reports per-row error.

    national_id, first_name, and last_name are mandatory in PERSON.
    Omitting last_name from the mapping means the field is missing.
    """
    csv_bytes = _make_csv(
        ["national_id", "first_name"],
        [
            ["123456789", "Alice"],
        ],
    )
    # Only map two fields — last_name (mandatory) is missing from data
    mapping = {"national_id": "national_id", "first_name": "first_name"}

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data=_import_data("PERSON", mapping, skip_errors="true"),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["failed"] == 1
    assert len(data["errors"]) == 1
    # Error should mention the missing required field
    error_text = data["errors"][0]["error"].lower()
    assert "last_name" in error_text or "required" in error_text or "mandatory" in error_text


@pytest.mark.asyncio
async def test_import_type_mismatch(client: AsyncClient, auth_headers: dict):
    """Import with type mismatch (string where integer expected) reports error.

    The age field in PERSON is type 'integer'. Passing 'not_a_number'
    should trigger a validation error.
    """
    csv_bytes = _make_csv(
        ["national_id", "first_name", "last_name", "age"],
        [
            ["123456789", "Alice", "Smith", "not_a_number"],
        ],
    )
    mapping = {
        "national_id": "national_id",
        "first_name": "first_name",
        "last_name": "last_name",
        "age": "age",
    }

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data=_import_data("PERSON", mapping, skip_errors="true"),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["failed"] == 1
    assert len(data["errors"]) == 1


@pytest.mark.asyncio
async def test_import_partial_success(client: AsyncClient, auth_headers: dict):
    """Import where some rows are valid and some invalid — both reported.

    Row 1: valid (all mandatory fields present)
    Row 2: invalid (empty first_name → missing mandatory)
    Row 3: valid
    Row 4: invalid (empty national_id → missing mandatory)
    """
    csv_text = (
        'national_id,first_name,last_name\r\n'
        '123456789,Alice,Smith\r\n'
        '222222222,,Jones\r\n'
        '333333333,Carol,Lee\r\n'
        ',Dave,Brown\r\n'
    )
    csv_bytes = _make_csv_raw(csv_text)
    mapping = {"national_id": "national_id", "first_name": "first_name", "last_name": "last_name"}

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data=_import_data("PERSON", mapping, skip_errors="true"),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_rows"] == 4
    assert data["succeeded"] == 2
    assert data["failed"] == 2
    assert len(data["errors"]) == 2
    # Check that error rows are correctly identified (rows 3 and 5 in file, i.e. row_num 3, 5)
    error_rows = {e["row"] for e in data["errors"]}
    assert 3 in error_rows  # empty first_name
    assert 5 in error_rows  # empty national_id


# ============================================================================
# Bulk Import (JSON)
# ============================================================================


@pytest.mark.asyncio
async def test_bulk_create_multiple_documents(client: AsyncClient, auth_headers: dict):
    """Import multiple documents via JSON bulk endpoint — all created."""
    items = [
        {
            "template_id": "PERSON",
            "namespace": "wip",
            "data": {
                "national_id": "111111111",
                "first_name": "Alice",
                "last_name": "Smith",
            },
        },
        {
            "template_id": "PERSON",
            "namespace": "wip",
            "data": {
                "national_id": "222222222",
                "first_name": "Bob",
                "last_name": "Jones",
            },
        },
        {
            "template_id": "PERSON",
            "namespace": "wip",
            "data": {
                "national_id": "333333333",
                "first_name": "Carol",
                "last_name": "Lee",
            },
        },
    ]

    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=items,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["succeeded"] == 3
    assert data["failed"] == 0
    assert all(r["status"] == "created" for r in data["results"])


@pytest.mark.asyncio
async def test_bulk_import_duplicate_identity_creates_new_version(client: AsyncClient, auth_headers: dict):
    """Import with duplicate identity (same national_id) creates a new version.

    The identity_fields for PERSON is ["national_id"], so submitting
    the same national_id twice should create version 1 then version 2.
    """
    doc = {
        "template_id": "PERSON",
        "namespace": "wip",
        "data": {
            "national_id": "999888777",
            "first_name": "Alice",
            "last_name": "Smith",
        },
    }

    # First create
    response1 = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[doc],
    )
    assert response1.status_code == 200
    data1 = response1.json()
    assert data1["succeeded"] == 1
    assert data1["results"][0]["is_new"] is True
    doc_id = data1["results"][0]["document_id"]
    assert data1["results"][0]["version"] == 1

    # Second create with same identity but different data
    doc_v2 = {
        "template_id": "PERSON",
        "namespace": "wip",
        "data": {
            "national_id": "999888777",
            "first_name": "Alice Updated",
            "last_name": "Smith-Jones",
        },
    }
    response2 = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[doc_v2],
    )
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["succeeded"] == 1
    # Same document_id, new version
    assert data2["results"][0]["document_id"] == doc_id
    assert data2["results"][0]["is_new"] is False
    assert data2["results"][0]["version"] == 2


# ============================================================================
# Error Cases
# ============================================================================


@pytest.mark.asyncio
async def test_import_nonexistent_template(client: AsyncClient, auth_headers: dict):
    """Import with a non-existent template ID returns an error."""
    csv_bytes = _make_csv(
        ["name"],
        [["Alice"]],
    )
    mapping = {"name": "name"}

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data=_import_data("TPL-NONEXISTENT", mapping, skip_errors="true"),
    )
    # Resolution failure for unknown synonym returns 404 (correct — the
    # synonym doesn't exist in Registry). If resolution is bypassed, the
    # service returns 200 with per-row errors.
    assert response.status_code in (404, 200)
    if response.status_code == 200:
        data = response.json()
        assert data["failed"] == 1
        assert data["succeeded"] == 0
        assert len(data["errors"]) == 1
        error_text = data["errors"][0]["error"].lower()
        assert "template" in error_text or "not found" in error_text


@pytest.mark.asyncio
async def test_import_empty_file(client: AsyncClient, auth_headers: dict):
    """Import an empty file returns an appropriate error."""
    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("empty.csv", b"", "text/csv")},
        data=_import_data("PERSON", {"a": "b"}),
    )
    assert response.status_code == 200
    data = response.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_import_header_only_file(client: AsyncClient, auth_headers: dict):
    """Import a CSV with headers but no data rows returns an error."""
    csv_bytes = _make_csv(
        ["national_id", "first_name", "last_name"],
        [],  # no data rows
    )
    mapping = {"national_id": "national_id", "first_name": "first_name", "last_name": "last_name"}

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data=_import_data("PERSON", mapping),
    )
    assert response.status_code == 200
    data = response.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_import_no_column_mapping(client: AsyncClient, auth_headers: dict):
    """Import without column_mapping form field returns 422 (FastAPI validation)."""
    csv_bytes = _make_csv(["national_id"], [["123456789"]])

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data={"template_id": "PERSON", "namespace": "wip"},
    )
    # FastAPI requires column_mapping — should return 422 (unprocessable entity)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_import_column_mapping_not_a_dict(client: AsyncClient, auth_headers: dict):
    """Import with column_mapping that is valid JSON but not an object returns error."""
    csv_bytes = _make_csv(["national_id"], [["123456789"]])

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data={
            "template_id": "PERSON",
            "column_mapping": json.dumps(["not", "a", "dict"]),
            "namespace": "wip",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert "object" in data["error"].lower() or "dict" in data["error"].lower()


# ============================================================================
# XLSX Import
# ============================================================================


@pytest.mark.asyncio
async def test_import_xlsx_success(client: AsyncClient, auth_headers: dict):
    """Import an XLSX file with valid data creates documents."""
    xlsx_bytes = _make_xlsx(
        ["national_id", "first_name", "last_name"],
        [
            ["123456789", "Alice", "Smith"],
            ["987654321", "Bob", "Jones"],
        ],
    )
    mapping = {"national_id": "national_id", "first_name": "first_name", "last_name": "last_name"}

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data=_import_data("PERSON", mapping),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_rows"] == 2
    assert data["succeeded"] == 2
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_import_xlsx_with_extra_columns(client: AsyncClient, auth_headers: dict):
    """XLSX with extra columns not in mapping — ignored gracefully."""
    xlsx_bytes = _make_xlsx(
        ["national_id", "first_name", "last_name", "notes", "score"],
        [
            ["123456789", "Alice", "Smith", "Some note", 99],
            ["987654321", "Bob", "Jones", "Another note", 42],
        ],
    )
    mapping = {"national_id": "national_id", "first_name": "first_name", "last_name": "last_name"}

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data=_import_data("PERSON", mapping),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 2
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_preview_empty_xlsx(client: AsyncClient, auth_headers: dict):
    """Preview an empty XLSX file returns error."""
    wb = openpyxl.Workbook()
    # Remove the default sheet and add an empty one
    ws = wb.active
    ws.title = "Sheet1"
    # Save workbook with no data at all
    buf = io.BytesIO()
    wb.save(buf)
    xlsx_bytes = buf.getvalue()

    response = await client.post(
        "/api/document-store/import/preview",
        headers=auth_headers,
        files={"file": ("empty.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200
    data = response.json()
    # Empty XLSX should return an error (no rows)
    assert "error" in data


# ============================================================================
# CSV via file import with upsert behavior
# ============================================================================


@pytest.mark.asyncio
async def test_csv_import_duplicate_identity_upsert(client: AsyncClient, auth_headers: dict):
    """Import CSV twice with same identity fields — second import creates new versions."""
    csv_bytes_v1 = _make_csv(
        ["national_id", "first_name", "last_name"],
        [["123456789", "Alice", "Smith"]],
    )
    mapping = {"national_id": "national_id", "first_name": "first_name", "last_name": "last_name"}

    # First import
    response1 = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes_v1, "text/csv")},
        data=_import_data("PERSON", mapping),
    )
    assert response1.status_code == 200
    data1 = response1.json()
    assert data1["succeeded"] == 1
    doc_id = data1["results"][0]["document_id"]
    assert data1["results"][0]["is_new"] is True

    # Second import with same national_id but different name
    csv_bytes_v2 = _make_csv(
        ["national_id", "first_name", "last_name"],
        [["123456789", "Alice Updated", "Smith-Jones"]],
    )
    response2 = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes_v2, "text/csv")},
        data=_import_data("PERSON", mapping),
    )
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["succeeded"] == 1
    # Same document_id (stable identity), new version
    assert data2["results"][0]["document_id"] == doc_id
    assert data2["results"][0]["is_new"] is False
    assert data2["results"][0]["version"] == 2


# ============================================================================
# CSV with BOM (Byte Order Mark)
# ============================================================================


@pytest.mark.asyncio
async def test_csv_with_bom(client: AsyncClient, auth_headers: dict):
    """CSV with UTF-8 BOM is handled correctly (headers not prefixed with BOM)."""
    csv_text = 'national_id,first_name,last_name\r\n123456789,Alice,Smith\r\n'
    csv_bytes = b'\xef\xbb\xbf' + csv_text.encode("utf-8")  # UTF-8 BOM prefix
    mapping = {"national_id": "national_id", "first_name": "first_name", "last_name": "last_name"}

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data=_import_data("PERSON", mapping),
    )
    assert response.status_code == 200
    data = response.json()
    # BOM should be stripped (the code uses utf-8-sig decoding)
    assert data["succeeded"] == 1
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_csv_bom_preview(client: AsyncClient, auth_headers: dict):
    """Preview of CSV with BOM — headers should not have BOM prefix."""
    csv_text = 'national_id,first_name,last_name\r\n123456789,Alice,Smith\r\n'
    csv_bytes = b'\xef\xbb\xbf' + csv_text.encode("utf-8")

    response = await client.post(
        "/api/document-store/import/preview",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    data = response.json()
    # First header should be clean, not '\ufeffnational_id'
    assert data["headers"][0] == "national_id"


# ============================================================================
# Bulk JSON — error handling
# ============================================================================


@pytest.mark.asyncio
async def test_bulk_create_with_invalid_template(client: AsyncClient, auth_headers: dict):
    """Bulk create with non-existent template reports per-item error."""
    items = [
        {
            "template_id": "TPL-NONEXISTENT",
            "namespace": "wip",
            "data": {"name": "Alice"},
        },
    ]
    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=items,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["failed"] == 1
    assert data["results"][0]["status"] == "error"


@pytest.mark.asyncio
async def test_bulk_create_partial_success(client: AsyncClient, auth_headers: dict):
    """Bulk create with mix of valid and invalid items — partial success."""
    items = [
        {
            "template_id": "PERSON",
            "namespace": "wip",
            "data": {
                "national_id": "111111111",
                "first_name": "Good",
                "last_name": "Row",
            },
        },
        {
            "template_id": "PERSON",
            "namespace": "wip",
            "data": {
                # Missing mandatory national_id and first_name
                "last_name": "BadRow",
            },
        },
        {
            "template_id": "PERSON",
            "namespace": "wip",
            "data": {
                "national_id": "333333333",
                "first_name": "Another Good",
                "last_name": "Row",
            },
        },
    ]
    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=items,
        params={"continue_on_error": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["succeeded"] == 2
    assert data["failed"] == 1
    # Check statuses
    statuses = [r["status"] for r in data["results"]]
    assert statuses.count("created") == 2
    assert statuses.count("error") == 1


# ============================================================================
# Import with valid term value (positive case)
# ============================================================================


@pytest.mark.asyncio
async def test_import_valid_term_value(client: AsyncClient, auth_headers: dict):
    """Import with a valid term value succeeds."""
    csv_bytes = _make_csv(
        ["national_id", "first_name", "last_name", "gender"],
        [
            ["123456789", "Alice", "Smith", "F"],
        ],
    )
    mapping = {
        "national_id": "national_id",
        "first_name": "first_name",
        "last_name": "last_name",
        "gender": "gender",
    }

    response = await client.post(
        "/api/document-store/import",
        headers=auth_headers,
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        data=_import_data("PERSON", mapping),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    assert data["failed"] == 0
