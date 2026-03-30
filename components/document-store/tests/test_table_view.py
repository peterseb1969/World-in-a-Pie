"""Tests for the Table View API (flattened document views and CSV export)."""

import csv
import io

import pytest
from httpx import AsyncClient

# Re-use the create_one helper pattern from test_documents
from tests.conftest import SAMPLE_TEMPLATES


async def create_one(client: AsyncClient, auth_headers: dict, template_id: str, data: dict, **extra):
    """Create a single document via the bulk-first API and return the result item."""
    payload = {"namespace": "wip", "template_id": template_id, "data": data, **extra}
    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[payload],
    )
    assert response.status_code == 200, f"Create failed: {response.text}"
    bulk = response.json()
    assert bulk["succeeded"] >= 1
    return bulk["results"][0]


# ============================================================================
# Tests: Basic Table View
# ============================================================================

@pytest.mark.asyncio
async def test_table_view_empty(client: AsyncClient, auth_headers: dict):
    """Table view for a template with no documents."""
    response = await client.get(
        "/api/document-store/table/TPL-000001",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["template_id"] == "TPL-000001"
    assert data["template_value"] == "PERSON"
    assert data["total_documents"] == 0
    assert data["total_rows"] == 0
    assert data["rows"] == []
    assert data["pages"] == 0


@pytest.mark.asyncio
async def test_table_view_with_documents(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Table view returns documents as flattened rows."""
    # Create 3 documents with different identities
    for i in range(3):
        data = sample_person_data.copy()
        data["national_id"] = f"10000000{i}"
        await create_one(client, auth_headers, "TPL-000001", data)

    response = await client.get(
        "/api/document-store/table/TPL-000001",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["template_id"] == "TPL-000001"
    assert data["template_value"] == "PERSON"
    assert data["total_documents"] == 3
    assert data["total_rows"] == 3
    assert len(data["rows"]) == 3

    # Verify column definitions
    col_names = [c["name"] for c in data["columns"]]
    assert "national_id" in col_names
    assert "first_name" in col_names
    assert "last_name" in col_names

    # Verify row content includes metadata columns
    row = data["rows"][0]
    assert "_document_id" in row
    assert "_version" in row
    assert "_status" in row
    assert "_created_at" in row


@pytest.mark.asyncio
async def test_table_view_template_not_found(client: AsyncClient, auth_headers: dict):
    """Table view for a non-existent template returns 404."""
    response = await client.get(
        "/api/document-store/table/TPL-NONEXISTENT",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_table_view_columns_match_template(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Table view column definitions match the template fields."""
    await create_one(client, auth_headers, "TPL-000001", sample_person_data)

    response = await client.get(
        "/api/document-store/table/TPL-000001",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()

    columns = {c["name"]: c for c in data["columns"]}
    template_fields = {f["name"]: f for f in SAMPLE_TEMPLATES["TPL-000001"]["fields"]}

    # Every template field should have a corresponding column
    for field_name, field_def in template_fields.items():
        assert field_name in columns, f"Column missing for field: {field_name}"
        col = columns[field_name]
        assert col["type"] == field_def["type"]
        assert col["label"] == field_def.get("label", field_name)


@pytest.mark.asyncio
async def test_table_view_row_data_values(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Table view rows contain actual document data values."""
    await create_one(client, auth_headers, "TPL-000001", sample_person_data)

    response = await client.get(
        "/api/document-store/table/TPL-000001",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 1

    row = data["rows"][0]
    assert row["national_id"] == sample_person_data["national_id"]
    assert row["first_name"] == sample_person_data["first_name"]
    assert row["last_name"] == sample_person_data["last_name"]
    assert row["age"] == sample_person_data["age"]


@pytest.mark.asyncio
async def test_table_view_pagination(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Table view supports pagination."""
    # Create 5 documents
    for i in range(5):
        data = sample_person_data.copy()
        data["national_id"] = f"20000000{i}"
        await create_one(client, auth_headers, "TPL-000001", data)

    # Page 1 of 2 (3 per page)
    response = await client.get(
        "/api/document-store/table/TPL-000001?page=1&page_size=3",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_documents"] == 5
    assert len(data["rows"]) == 3
    assert data["page"] == 1
    assert data["page_size"] == 3
    assert data["pages"] == 2

    # Page 2
    response = await client.get(
        "/api/document-store/table/TPL-000001?page=2&page_size=3",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 2
    assert data["page"] == 2


@pytest.mark.asyncio
async def test_table_view_status_filter(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Table view filters by document status."""
    # Create a document and then archive it
    result = await create_one(client, auth_headers, "TPL-000001", sample_person_data)
    doc_id = result["document_id"]

    # Archive it
    await client.post(
        "/api/document-store/documents/archive",
        headers=auth_headers,
        json=[{"id": doc_id}],
    )

    # Create another active document
    data2 = sample_person_data.copy()
    data2["national_id"] = "987654321"
    await create_one(client, auth_headers, "TPL-000001", data2)

    # Default filter = active
    response = await client.get(
        "/api/document-store/table/TPL-000001",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["total_documents"] == 1

    # Filter: archived
    response = await client.get(
        "/api/document-store/table/TPL-000001?status=archived",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["total_documents"] == 1


@pytest.mark.asyncio
async def test_table_view_no_arrays(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Table view with no array fields has array_handling='none'."""
    await create_one(client, auth_headers, "TPL-000001", sample_person_data)

    response = await client.get(
        "/api/document-store/table/TPL-000001",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["array_handling"] == "none"


@pytest.mark.asyncio
async def test_table_view_different_templates(client: AsyncClient, auth_headers: dict, sample_person_data: dict, sample_employee_data: dict):
    """Table view returns only documents for the requested template."""
    # Create person and employee documents
    await create_one(client, auth_headers, "TPL-000001", sample_person_data)
    await create_one(client, auth_headers, "TPL-000002", sample_employee_data)

    # Person table
    response = await client.get(
        "/api/document-store/table/TPL-000001",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_documents"] == 1
    assert data["template_id"] == "TPL-000001"

    # Employee table
    response = await client.get(
        "/api/document-store/table/TPL-000002",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_documents"] == 1
    assert data["template_id"] == "TPL-000002"


# ============================================================================
# Tests: CSV Export
# ============================================================================

@pytest.mark.asyncio
async def test_csv_export_empty(client: AsyncClient, auth_headers: dict):
    """CSV export for template with no documents returns empty content."""
    response = await client.get(
        "/api/document-store/table/TPL-000001/csv",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert "PERSON.csv" in response.headers.get("content-disposition", "")
    assert response.text == ""


@pytest.mark.asyncio
async def test_csv_export_with_data(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """CSV export includes headers and data rows."""
    # Create 2 documents
    for i in range(2):
        data = sample_person_data.copy()
        data["national_id"] = f"30000000{i}"
        data["first_name"] = f"Person{i}"
        await create_one(client, auth_headers, "TPL-000001", data)

    response = await client.get(
        "/api/document-store/table/TPL-000001/csv",
        headers=auth_headers,
    )
    assert response.status_code == 200

    # Parse CSV
    reader = csv.DictReader(io.StringIO(response.text))
    rows = list(reader)
    assert len(rows) == 2

    # Check headers include metadata columns
    headers = reader.fieldnames
    assert "_document_id" in headers
    assert "_version" in headers
    assert "national_id" in headers
    assert "first_name" in headers

    # Check data
    first_names = {row["first_name"] for row in rows}
    assert "Person0" in first_names
    assert "Person1" in first_names


@pytest.mark.asyncio
async def test_csv_export_without_metadata(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """CSV export with include_metadata=false excludes system columns."""
    await create_one(client, auth_headers, "TPL-000001", sample_person_data)

    response = await client.get(
        "/api/document-store/table/TPL-000001/csv?include_metadata=false",
        headers=auth_headers,
    )
    assert response.status_code == 200

    reader = csv.DictReader(io.StringIO(response.text))
    rows = list(reader)
    assert len(rows) == 1

    headers = reader.fieldnames
    assert "_document_id" not in headers
    assert "_version" not in headers
    assert "national_id" in headers
    assert "first_name" in headers


@pytest.mark.asyncio
async def test_csv_export_template_not_found(client: AsyncClient, auth_headers: dict):
    """CSV export for a non-existent template returns 404."""
    response = await client.get(
        "/api/document-store/table/TPL-NONEXISTENT/csv",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_csv_export_filename(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """CSV export Content-Disposition uses template value as filename."""
    await create_one(client, auth_headers, "TPL-000001", sample_person_data)

    response = await client.get(
        "/api/document-store/table/TPL-000001/csv",
        headers=auth_headers,
    )
    assert response.status_code == 200
    disposition = response.headers.get("content-disposition", "")
    assert "PERSON.csv" in disposition


@pytest.mark.asyncio
async def test_csv_export_employee_template(client: AsyncClient, auth_headers: dict, sample_employee_data: dict):
    """CSV export works for employee template with all fields."""
    await create_one(client, auth_headers, "TPL-000002", sample_employee_data)

    response = await client.get(
        "/api/document-store/table/TPL-000002/csv",
        headers=auth_headers,
    )
    assert response.status_code == 200

    reader = csv.DictReader(io.StringIO(response.text))
    rows = list(reader)
    assert len(rows) == 1

    row = rows[0]
    assert row["employee_id"] == sample_employee_data["employee_id"]
    assert row["name"] == sample_employee_data["name"]
    assert row["department"] == sample_employee_data["department"]


# ============================================================================
# Tests: Table View with Array Field Expansion
# ============================================================================

@pytest.mark.asyncio
async def test_table_view_array_handling_label(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Table view sets array_handling based on template fields.

    The PERSON template has no array fields, so array_handling should be 'none'.
    This test documents the baseline behavior before array fields are introduced.
    """
    await create_one(client, auth_headers, "TPL-000001", sample_person_data)

    response = await client.get(
        "/api/document-store/table/TPL-000001",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["array_handling"] == "none"

    # No columns should be marked as arrays
    for col in data["columns"]:
        assert col["is_array"] is False
        assert col["is_flattened"] is False


# ============================================================================
# Tests: Auth Required
# ============================================================================

@pytest.mark.asyncio
async def test_table_view_requires_auth(client: AsyncClient):
    """Table view requires authentication."""
    response = await client.get(
        "/api/document-store/table/TPL-000001",
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_csv_export_requires_auth(client: AsyncClient):
    """CSV export requires authentication."""
    response = await client.get(
        "/api/document-store/table/TPL-000001/csv",
    )
    assert response.status_code == 401
