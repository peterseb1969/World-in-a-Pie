"""Tests for term deprecation, term/terminology restore, and dependency checking."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient


@pytest_asyncio.fixture
async def test_terminology(client: AsyncClient, auth_headers: dict):
    """Create a test terminology for deprecation/restore tests."""
    response = await client.post(
        "/api/def-store/terminologies",
        headers=auth_headers,
        json=[{
            "value": "DEPRECATION_TEST",
            "label": "Deprecation Test",
            "namespace": "wip",
            "case_sensitive": False
        }]
    )
    data = response.json()
    terminology_id = data["results"][0]["id"]

    get_response = await client.get(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers
    )
    return get_response.json()


@pytest_asyncio.fixture
async def terminology_with_terms(client: AsyncClient, auth_headers: dict, test_terminology):
    """Create a terminology with two terms for deprecation testing."""
    terminology_id = test_terminology["terminology_id"]

    # Create first term
    t1_resp = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "old_value",
            "label": "Old Value",
            "description": "A term that will be deprecated"
        }]
    )
    term1_id = t1_resp.json()["results"][0]["id"]

    # Create second term (replacement)
    t2_resp = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/terms",
        headers=auth_headers,
        json=[{
            "value": "new_value",
            "label": "New Value",
            "description": "The replacement term"
        }]
    )
    term2_id = t2_resp.json()["results"][0]["id"]

    return {
        "terminology_id": terminology_id,
        "term1_id": term1_id,
        "term2_id": term2_id,
    }


# =============================================================================
# DEPRECATE TERMS
# POST /api/def-store/terms/deprecate
# =============================================================================

@pytest.mark.asyncio
async def test_deprecate_term_with_replacement(
    client: AsyncClient, auth_headers: dict, terminology_with_terms: dict
):
    """Test deprecating a term with a replacement term specified."""
    term_id = terminology_with_terms["term1_id"]
    replacement_id = terminology_with_terms["term2_id"]

    response = await client.post(
        "/api/def-store/terms/deprecate",
        headers=auth_headers,
        json=[{
            "term_id": term_id,
            "reason": "Replaced by new_value",
            "replaced_by_term_id": replacement_id
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["succeeded"] == 1
    assert data["failed"] == 0
    assert data["results"][0]["status"] == "updated"
    assert data["results"][0]["id"] == term_id

    # Verify the term is now deprecated via GET
    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    assert get_response.status_code == 200
    term_data = get_response.json()
    assert term_data["status"] == "deprecated"
    assert term_data["deprecated_reason"] == "Replaced by new_value"
    assert term_data["replaced_by_term_id"] == replacement_id


@pytest.mark.asyncio
async def test_deprecate_term_without_replacement(
    client: AsyncClient, auth_headers: dict, terminology_with_terms: dict
):
    """Test deprecating a term without specifying a replacement."""
    term_id = terminology_with_terms["term1_id"]

    response = await client.post(
        "/api/def-store/terms/deprecate",
        headers=auth_headers,
        json=[{
            "term_id": term_id,
            "reason": "No longer in use"
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    assert data["results"][0]["status"] == "updated"

    # Verify the term is deprecated but has no replacement
    get_response = await client.get(
        f"/api/def-store/terms/{term_id}",
        headers=auth_headers
    )
    term_data = get_response.json()
    assert term_data["status"] == "deprecated"
    assert term_data["deprecated_reason"] == "No longer in use"
    assert term_data["replaced_by_term_id"] is None


@pytest.mark.asyncio
async def test_deprecate_nonexistent_term(
    client: AsyncClient, auth_headers: dict
):
    """Test deprecating a term that does not exist."""
    response = await client.post(
        "/api/def-store/terms/deprecate",
        headers=auth_headers,
        json=[{
            "term_id": "T-NONEXISTENT",
            "reason": "Should fail"
        }]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["succeeded"] == 0
    assert data["failed"] == 1
    assert data["results"][0]["status"] == "error"
    assert "not found" in data["results"][0]["error"].lower()


@pytest.mark.asyncio
async def test_deprecate_term_updates_terminology_term_count(
    client: AsyncClient, auth_headers: dict, terminology_with_terms: dict
):
    """Test that deprecating a term decrements the active term count."""
    terminology_id = terminology_with_terms["terminology_id"]
    term_id = terminology_with_terms["term1_id"]

    # Check initial term count
    get_resp = await client.get(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers
    )
    initial_count = get_resp.json()["term_count"]

    # Deprecate the term
    await client.post(
        "/api/def-store/terms/deprecate",
        headers=auth_headers,
        json=[{
            "term_id": term_id,
            "reason": "Testing count update"
        }]
    )

    # Check term count is decremented
    get_resp = await client.get(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers
    )
    new_count = get_resp.json()["term_count"]
    assert new_count == initial_count - 1


@pytest.mark.asyncio
async def test_deprecate_multiple_terms_bulk(
    client: AsyncClient, auth_headers: dict, test_terminology: dict
):
    """Test deprecating multiple terms at once via bulk endpoint."""
    terminology_id = test_terminology["terminology_id"]

    # Create three terms
    term_ids = []
    for val in ["bulk_a", "bulk_b", "bulk_c"]:
        resp = await client.post(
            f"/api/def-store/terminologies/{terminology_id}/terms",
            headers=auth_headers,
            json=[{"value": val, "label": val.title()}]
        )
        term_ids.append(resp.json()["results"][0]["id"])

    # Deprecate all three in one request
    response = await client.post(
        "/api/def-store/terms/deprecate",
        headers=auth_headers,
        json=[
            {"term_id": term_ids[0], "reason": "Bulk deprecation 1"},
            {"term_id": term_ids[1], "reason": "Bulk deprecation 2"},
            {"term_id": term_ids[2], "reason": "Bulk deprecation 3"},
        ]
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["succeeded"] == 3
    assert data["failed"] == 0

    # Verify all are deprecated
    for tid in term_ids:
        get_resp = await client.get(
            f"/api/def-store/terms/{tid}",
            headers=auth_headers
        )
        assert get_resp.json()["status"] == "deprecated"


@pytest.mark.asyncio
async def test_deprecate_requires_authentication(client: AsyncClient):
    """Test that deprecation endpoint requires authentication."""
    response = await client.post(
        "/api/def-store/terms/deprecate",
        json=[{
            "term_id": "T-000001",
            "reason": "Should fail auth"
        }]
    )
    assert response.status_code == 401


# =============================================================================
# RESTORE TERMINOLOGY
# POST /api/def-store/terminologies/{terminology_id}/restore
# =============================================================================

@pytest.mark.asyncio
async def test_restore_deleted_terminology(
    client: AsyncClient, auth_headers: dict, terminology_with_terms: dict
):
    """Test restoring a soft-deleted terminology."""
    terminology_id = terminology_with_terms["terminology_id"]

    # Delete the terminology
    delete_resp = await client.request(
        "DELETE",
        "/api/def-store/terminologies",
        headers=auth_headers,
        content=f'[{{"id": "{terminology_id}", "force": true}}]'
    )
    assert delete_resp.json()["succeeded"] == 1

    # Verify it is inactive
    get_resp = await client.get(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers
    )
    assert get_resp.json()["status"] == "inactive"

    # Restore it
    restore_resp = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/restore",
        headers=auth_headers
    )

    assert restore_resp.status_code == 200
    restored_data = restore_resp.json()
    assert restored_data["status"] == "active"
    assert restored_data["terminology_id"] == terminology_id


@pytest.mark.asyncio
async def test_restore_terminology_also_restores_terms(
    client: AsyncClient, auth_headers: dict, terminology_with_terms: dict
):
    """Test that restoring a terminology also reactivates its terms by default."""
    terminology_id = terminology_with_terms["terminology_id"]
    term1_id = terminology_with_terms["term1_id"]
    term2_id = terminology_with_terms["term2_id"]

    # Delete the terminology (which deactivates all terms)
    await client.request(
        "DELETE",
        "/api/def-store/terminologies",
        headers=auth_headers,
        content=f'[{{"id": "{terminology_id}", "force": true}}]'
    )

    # Verify terms are inactive
    for tid in [term1_id, term2_id]:
        get_resp = await client.get(
            f"/api/def-store/terms/{tid}",
            headers=auth_headers
        )
        assert get_resp.json()["status"] == "inactive"

    # Restore with restore_terms=true (default)
    restore_resp = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/restore",
        headers=auth_headers
    )
    assert restore_resp.status_code == 200
    assert restore_resp.json()["status"] == "active"

    # Verify terms are reactivated
    for tid in [term1_id, term2_id]:
        get_resp = await client.get(
            f"/api/def-store/terms/{tid}",
            headers=auth_headers
        )
        assert get_resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_restore_terminology_without_restoring_terms(
    client: AsyncClient, auth_headers: dict, terminology_with_terms: dict
):
    """Test restoring a terminology without reactivating its terms."""
    terminology_id = terminology_with_terms["terminology_id"]
    term1_id = terminology_with_terms["term1_id"]

    # Delete the terminology
    await client.request(
        "DELETE",
        "/api/def-store/terminologies",
        headers=auth_headers,
        content=f'[{{"id": "{terminology_id}", "force": true}}]'
    )

    # Restore with restore_terms=false
    restore_resp = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/restore",
        headers=auth_headers,
        params={"restore_terms": False}
    )
    assert restore_resp.status_code == 200
    assert restore_resp.json()["status"] == "active"

    # Terms should still be inactive
    get_resp = await client.get(
        f"/api/def-store/terms/{term1_id}",
        headers=auth_headers
    )
    assert get_resp.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_restore_already_active_terminology(
    client: AsyncClient, auth_headers: dict, test_terminology: dict
):
    """Test that restoring an already-active terminology is a no-op (returns it as-is)."""
    terminology_id = test_terminology["terminology_id"]

    restore_resp = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/restore",
        headers=auth_headers
    )

    assert restore_resp.status_code == 200
    assert restore_resp.json()["status"] == "active"
    assert restore_resp.json()["terminology_id"] == terminology_id


@pytest.mark.asyncio
async def test_restore_nonexistent_terminology(
    client: AsyncClient, auth_headers: dict
):
    """Test that restoring a nonexistent terminology returns 404."""
    restore_resp = await client.post(
        "/api/def-store/terminologies/TERM-NONEXISTENT/restore",
        headers=auth_headers
    )

    assert restore_resp.status_code == 404


@pytest.mark.asyncio
async def test_restore_terminology_requires_authentication(client: AsyncClient):
    """Test that restore endpoint requires authentication."""
    response = await client.post(
        "/api/def-store/terminologies/TERM-000001/restore"
    )
    assert response.status_code == 401


# =============================================================================
# RESTORE TERM (via terminology restore)
# =============================================================================

@pytest.mark.asyncio
async def test_restore_deleted_term_via_terminology_restore(
    client: AsyncClient, auth_headers: dict, terminology_with_terms: dict
):
    """Test restoring a deleted term by restoring its parent terminology."""
    terminology_id = terminology_with_terms["terminology_id"]
    term1_id = terminology_with_terms["term1_id"]

    # Delete individual term
    await client.request(
        "DELETE",
        "/api/def-store/terms",
        headers=auth_headers,
        content=f'[{{"id": "{term1_id}"}}]'
    )

    # Verify term is inactive
    get_resp = await client.get(
        f"/api/def-store/terms/{term1_id}",
        headers=auth_headers
    )
    assert get_resp.json()["status"] == "inactive"

    # Delete the terminology (to make it inactive)
    await client.request(
        "DELETE",
        "/api/def-store/terminologies",
        headers=auth_headers,
        content=f'[{{"id": "{terminology_id}", "force": true}}]'
    )

    # Restore the terminology with restore_terms=true
    restore_resp = await client.post(
        f"/api/def-store/terminologies/{terminology_id}/restore",
        headers=auth_headers,
        params={"restore_terms": True}
    )
    assert restore_resp.status_code == 200

    # The individually-deleted term should also be restored
    # (restore_terminology reactivates all inactive terms)
    get_resp = await client.get(
        f"/api/def-store/terms/{term1_id}",
        headers=auth_headers
    )
    assert get_resp.json()["status"] == "active"


# =============================================================================
# TERMINOLOGY DEPENDENCIES
# GET /api/def-store/terminologies/{terminology_id}/dependencies
# =============================================================================

@pytest.mark.asyncio
async def test_get_terminology_dependencies_no_deps(
    client: AsyncClient, auth_headers: dict, test_terminology: dict
):
    """Test checking dependencies for a terminology with no dependents.

    The dependency check calls Template Store, which is mocked via
    _get_referencing_templates to return an empty template list.
    """
    terminology_id = test_terminology["terminology_id"]

    # Mock only the HTTP call portion — let the DB lookup run normally
    mock_get_templates = AsyncMock(return_value=[])

    with patch(
        "def_store.services.dependency_service.DependencyService._get_referencing_templates",
        mock_get_templates,
    ):
        response = await client.get(
            f"/api/def-store/terminologies/{terminology_id}/dependencies",
            headers=auth_headers
        )

    assert response.status_code == 200
    data = response.json()
    assert data["terminology_id"] == terminology_id
    assert data["template_count"] == 0
    assert data["has_dependencies"] is False
    assert data["can_deactivate"] is True


@pytest.mark.asyncio
async def test_get_terminology_dependencies_with_deps(
    client: AsyncClient, auth_headers: dict, test_terminology: dict
):
    """Test checking dependencies when templates reference this terminology."""
    terminology_id = test_terminology["terminology_id"]
    terminology_value = test_terminology["value"]

    # Simulate _get_referencing_templates returning one matching template
    mock_referencing = [
        {
            "template_id": "TPL-000001",
            "value": "patient_form",
            "label": "Patient Form",
            "field": "status",
        }
    ]

    mock_get_templates = AsyncMock(return_value=mock_referencing)

    with patch(
        "def_store.services.dependency_service.DependencyService._get_referencing_templates",
        mock_get_templates,
    ):
        response = await client.get(
            f"/api/def-store/terminologies/{terminology_id}/dependencies",
            headers=auth_headers
        )

    assert response.status_code == 200
    data = response.json()
    assert data["terminology_id"] == terminology_id
    assert data["template_count"] == 1
    assert data["has_dependencies"] is True
    assert data["can_deactivate"] is True  # Can still deactivate, but with warning
    assert "1 template" in data["warning_message"]
    assert len(data["templates"]) == 1
    assert data["templates"][0]["template_id"] == "TPL-000001"


@pytest.mark.asyncio
async def test_get_dependencies_nonexistent_terminology(
    client: AsyncClient, auth_headers: dict
):
    """Test checking dependencies for a terminology that does not exist."""
    response = await client.get(
        "/api/def-store/terminologies/TERM-NONEXISTENT/dependencies",
        headers=auth_headers
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_terminology_blocked_by_dependencies(
    client: AsyncClient, auth_headers: dict, test_terminology: dict
):
    """Test that deleting a terminology with dependencies is blocked (without force)."""
    terminology_id = test_terminology["terminology_id"]

    # Simulate _get_referencing_templates returning one matching template
    mock_referencing = [
        {
            "template_id": "TPL-000001",
            "value": "patient_form",
            "label": "Patient Form",
            "field": "status",
        }
    ]

    mock_get_templates = AsyncMock(return_value=mock_referencing)

    with patch(
        "def_store.services.dependency_service.DependencyService._get_referencing_templates",
        mock_get_templates,
    ):
        # Try delete without force
        response = await client.request(
            "DELETE",
            "/api/def-store/terminologies",
            headers=auth_headers,
            content=f'[{{"id": "{terminology_id}"}}]'
        )

    assert response.status_code == 200
    data = response.json()
    assert data["failed"] == 1
    assert data["succeeded"] == 0
    assert "dependent templates" in data["results"][0]["error"].lower()


@pytest.mark.asyncio
async def test_delete_terminology_with_force_bypasses_dependencies(
    client: AsyncClient, auth_headers: dict, test_terminology: dict
):
    """Test that deleting a terminology with force=true succeeds despite dependencies."""
    terminology_id = test_terminology["terminology_id"]

    # No need to mock Template Store because force=true skips the dependency check
    response = await client.request(
        "DELETE",
        "/api/def-store/terminologies",
        headers=auth_headers,
        content=f'[{{"id": "{terminology_id}", "force": true}}]'
    )

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    assert data["results"][0]["status"] == "deleted"

    # Verify it is inactive
    get_resp = await client.get(
        f"/api/def-store/terminologies/{terminology_id}",
        headers=auth_headers
    )
    assert get_resp.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_dependencies_requires_authentication(client: AsyncClient):
    """Test that dependencies endpoint requires authentication."""
    response = await client.get(
        "/api/def-store/terminologies/TERM-000001/dependencies"
    )
    assert response.status_code == 401


# =============================================================================
# TEMPLATE STORE UNAVAILABLE DURING DEPENDENCY CHECK
# =============================================================================

@pytest.mark.asyncio
async def test_dependency_check_when_template_store_unavailable(
    client: AsyncClient, auth_headers: dict, test_terminology: dict
):
    """Test dependency check when Template Store is unreachable."""
    terminology_id = test_terminology["terminology_id"]

    # Mock the Template Store to raise a connection error
    with patch("def_store.services.dependency_service.httpx.AsyncClient") as MockHttpClient:
        mock_client_instance = AsyncMock()
        mock_client_instance.get.side_effect = Exception("Connection refused")
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)
        MockHttpClient.return_value = mock_client_instance

        response = await client.get(
            f"/api/def-store/terminologies/{terminology_id}/dependencies",
            headers=auth_headers
        )

    assert response.status_code == 200
    data = response.json()
    assert data["terminology_id"] == terminology_id
    # template_count is -1 when Template Store is unavailable
    assert data["template_count"] == -1
    assert "unavailable" in data["warning_message"].lower()
