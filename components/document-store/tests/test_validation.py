"""Tests for document validation."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_validate_valid_document(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test validating a valid document."""
    response = await client.post(
        "/api/document-store/validation/validate",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": sample_person_data
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True
    assert data["errors"] == []
    assert data["identity_hash"] is not None
    assert data["template_version"] == 1


@pytest.mark.asyncio
async def test_validate_missing_required_field(client: AsyncClient, auth_headers: dict):
    """Test validation fails for missing required field."""
    response = await client.post(
        "/api/document-store/validation/validate",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": {
                "national_id": "123456789",
                # Missing first_name and last_name (required)
            }
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is False
    assert len(data["errors"]) >= 2  # first_name and last_name missing

    # Check error codes
    error_codes = [e["code"] for e in data["errors"]]
    assert "required" in error_codes


@pytest.mark.asyncio
async def test_validate_invalid_type(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test validation fails for wrong type."""
    data = sample_person_data.copy()
    data["age"] = "not a number"  # Should be integer

    response = await client.post(
        "/api/document-store/validation/validate",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": data
        }
    )

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is False

    # Find the type error
    type_errors = [e for e in result["errors"] if e["code"] == "invalid_type"]
    assert len(type_errors) >= 1
    assert type_errors[0]["field"] == "age"


@pytest.mark.asyncio
async def test_validate_pattern_mismatch(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test validation fails for pattern mismatch."""
    data = sample_person_data.copy()
    data["national_id"] = "invalid"  # Should be 9 digits

    response = await client.post(
        "/api/document-store/validation/validate",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": data
        }
    )

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is False

    pattern_errors = [e for e in result["errors"] if e["code"] == "pattern"]
    assert len(pattern_errors) >= 1


@pytest.mark.asyncio
async def test_validate_invalid_term(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test validation fails for invalid term value."""
    data = sample_person_data.copy()
    data["gender"] = "INVALID"  # Not a valid gender term

    response = await client.post(
        "/api/document-store/validation/validate",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": data
        }
    )

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is False

    term_errors = [e for e in result["errors"] if e["code"] == "invalid_term"]
    assert len(term_errors) >= 1


@pytest.mark.asyncio
async def test_validate_number_out_of_range(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test validation fails for number out of range."""
    data = sample_person_data.copy()
    data["age"] = 200  # Max is 150

    response = await client.post(
        "/api/document-store/validation/validate",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": data
        }
    )

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is False

    max_errors = [e for e in result["errors"] if e["code"] == "maximum"]
    assert len(max_errors) >= 1


@pytest.mark.asyncio
async def test_validate_invalid_date(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test validation fails for invalid date format."""
    data = sample_person_data.copy()
    data["birth_date"] = "not-a-date"

    response = await client.post(
        "/api/document-store/validation/validate",
        headers=auth_headers,
        json={
            "template_id": "TPL-000001",
            "data": data
        }
    )

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is False

    type_errors = [e for e in result["errors"] if e["code"] == "invalid_type"]
    assert len(type_errors) >= 1


@pytest.mark.asyncio
async def test_validate_template_not_found(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test validation fails for non-existent template."""
    response = await client.post(
        "/api/document-store/validation/validate",
        headers=auth_headers,
        json={
            "template_id": "TPL-NOTFOUND",
            "data": sample_person_data
        }
    )

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is False

    template_errors = [e for e in result["errors"] if e["code"] == "template_not_found"]
    assert len(template_errors) >= 1


@pytest.mark.asyncio
async def test_validate_inactive_template(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Test validation fails for inactive template."""
    response = await client.post(
        "/api/document-store/validation/validate",
        headers=auth_headers,
        json={
            "template_id": "TPL-INACTIVE",
            "data": sample_person_data
        }
    )

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is False

    template_errors = [e for e in result["errors"] if e["code"] == "template_inactive"]
    assert len(template_errors) >= 1


@pytest.mark.asyncio
async def test_validate_conditional_required_rule(client: AsyncClient, auth_headers: dict, sample_employee_data: dict):
    """Test conditional_required rule validation."""
    data = sample_employee_data.copy()
    del data["manager_id"]  # Remove manager_id but keep department

    response = await client.post(
        "/api/document-store/validation/validate",
        headers=auth_headers,
        json={
            "template_id": "TPL-000002",
            "data": data
        }
    )

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is False

    rule_errors = [e for e in result["errors"] if e["code"] == "conditional_required"]
    assert len(rule_errors) >= 1


@pytest.mark.asyncio
async def test_validate_conditional_required_passes(client: AsyncClient, auth_headers: dict, sample_employee_data: dict):
    """Test conditional_required rule passes when condition not met."""
    data = sample_employee_data.copy()
    del data["department"]  # Remove department, so manager_id not required
    del data["manager_id"]

    response = await client.post(
        "/api/document-store/validation/validate",
        headers=auth_headers,
        json={
            "template_id": "TPL-000002",
            "data": data
        }
    )

    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is True


@pytest.mark.asyncio
async def test_create_document_validation_error(client: AsyncClient, auth_headers: dict):
    """Test that creating a document with invalid data returns error in BulkResponse."""
    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{
            "template_id": "TPL-000001",
            "data": {
                "national_id": "invalid"  # Missing required fields and invalid format
            }
        }]
    )

    assert response.status_code == 200
    bulk = response.json()
    assert bulk["failed"] == 1
    assert bulk["succeeded"] == 0
    assert bulk["results"][0]["status"] == "error"
    assert bulk["results"][0]["error"] is not None
