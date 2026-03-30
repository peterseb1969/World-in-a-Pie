"""Tests for import/export API endpoints."""

import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def populated_terminology(client: AsyncClient, auth_headers: dict):
    """Create a terminology with terms for export tests."""
    # Create terminology
    term_response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "EXPORT_TEST",
            "label": "Export Test Terminology",
            "namespace": "wip",
            "description": "For testing export functionality"
        }]
    )
    terminology_id = term_response.json()["results"][0]["id"]

    # Add terms
    terms = [
        {"value": "option1", "label": "Option 1", "sort_order": 1},
        {"value": "option2", "label": "Option 2", "sort_order": 2},
        {"value": "option3", "label": "Option 3", "sort_order": 3},
    ]

    await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=terms
    )

    # Fetch the full terminology for the fixture return value
    get_response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers
    )
    return get_response.json()


@pytest.mark.asyncio
async def test_export_terminology_json(client: AsyncClient, auth_headers: dict, populated_terminology):
    """Test exporting a terminology as JSON."""
    terminology_id = populated_terminology["terminology_id"]

    response = await client.get(
        f"/api/def-store/import-export/export/{terminology_id}?format=json",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()

    assert "terminology" in data
    assert "terms" in data
    assert data["terminology"]["value"] == "EXPORT_TEST"
    assert len(data["terms"]) == 3


@pytest.mark.asyncio
async def test_export_terminology_csv(client: AsyncClient, auth_headers: dict, populated_terminology):
    """Test exporting a terminology as CSV."""
    terminology_id = populated_terminology["terminology_id"]

    response = await client.get(
        f"/api/def-store/import-export/export/{terminology_id}?format=csv",
        headers=auth_headers
    )

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]

    # Check CSV content
    content = response.text
    lines = content.strip().split("\n")
    assert len(lines) == 4  # header + 3 terms
    assert "value" in lines[0].lower()
    assert "value" in lines[0].lower()


@pytest.mark.asyncio
async def test_export_all_terminologies(client: AsyncClient, auth_headers: dict):
    """Test exporting all terminologies."""
    # Create a couple terminologies
    for i in range(2):
        await client.post(
            "/api/def-store/terminologies",
            headers=auth_headers,
            json=[{
                "value": f"EXPORT_ALL_{i}",
                "label": f"Export All Test {i}",
                "namespace": "wip"
            }]
        )

    response = await client.get(
        "/api/def-store/import-export/export",
        headers=auth_headers
    )

    assert response.status_code == 200
    data = response.json()
    assert "terminologies" in data
    assert data["count"] >= 2


@pytest.mark.asyncio
async def test_import_terminology_json(client: AsyncClient, auth_headers: dict):
    """Test importing a terminology from JSON."""
    import_data = {
        "terminology": {
            "value": "IMPORTED",
            "label": "Imported Terminology",
            "namespace": "wip",
            "description": "This was imported"
        },
        "terms": [
            {"value": "term1", "label": "Term 1", "sort_order": 1},
            {"value": "term2", "label": "Term 2", "sort_order": 2}
        ]
    }

    response = await client.post(
        "/api/def-store/import-export/import?format=json",
        headers=auth_headers,
        json=import_data
    )

    assert response.status_code == 200
    data = response.json()
    assert data["terminology"]["value"] == "IMPORTED"
    assert data["terms_result"]["succeeded"] == 2

    # Verify terminology was created
    verify_response = await client.get(
        "/api/def-store/terminologies/by-value/IMPORTED",
        headers=auth_headers
    )
    assert verify_response.status_code == 200


@pytest.mark.asyncio
async def test_import_terminology_with_update(client: AsyncClient, auth_headers: dict):
    """Test importing with update_existing flag."""
    # First import
    import_data = {
        "terminology": {
            "value": "UPDATE_TEST",
            "label": "Original Name",
            "namespace": "wip"
        },
        "terms": [
            {"value": "value1", "label": "Value 1"}
        ]
    }

    await client.post(
        "/api/def-store/import-export/import?format=json",
        headers=auth_headers,
        json=import_data
    )

    # Second import with update
    import_data["terminology"]["label"] = "Updated Name"
    import_data["terms"].append({"value": "value2", "label": "Value 2"})

    response = await client.post(
        "/api/def-store/import-export/import?format=json&update_existing=true",
        headers=auth_headers,
        json=import_data
    )

    assert response.status_code == 200
    data = response.json()
    # Should have created 1 new term (V2), skipped 1 (V1 - still skips with update_existing)
    assert data["terms_result"]["succeeded"] >= 1


@pytest.mark.asyncio
async def test_export_not_found(client: AsyncClient, auth_headers: dict):
    """Test exporting a non-existent terminology."""
    response = await client.get(
        "/api/def-store/import-export/export/TERM-999999?format=json",
        headers=auth_headers
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_import_invalid_format(client: AsyncClient, auth_headers: dict):
    """Test importing with invalid data format."""
    response = await client.post(
        "/api/def-store/import-export/import?format=json",
        headers=auth_headers,
        json={"invalid": "data"}
    )

    assert response.status_code == 400
