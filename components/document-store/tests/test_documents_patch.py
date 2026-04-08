"""Tests for PATCH /documents (RFC 7396 partial updates).

Covers the per-item error code matrix from docs/design/document-patch.md
plus happy paths, the no-op detection rule, and a representative bulk
mixed-success scenario.
"""

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def create_one(
    client: AsyncClient,
    auth_headers: dict,
    template_id: str,
    data: dict,
    **extra,
):
    """Create a single document via the bulk-first POST endpoint."""
    payload = {"namespace": "wip", "template_id": template_id, "data": data, **extra}
    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[payload],
    )
    assert response.status_code == 200, f"Create failed: {response.text}"
    bulk = response.json()
    assert bulk["succeeded"] == 1, f"Create not successful: {bulk}"
    return bulk["results"][0]


async def patch_one(
    client: AsyncClient,
    auth_headers: dict,
    document_id: str,
    patch: dict,
    if_match: int | None = None,
):
    """Send a single PATCH item and return the (top-level bulk, single result)."""
    item: dict = {"document_id": document_id, "patch": patch}
    if if_match is not None:
        item["if_match"] = if_match
    response = await client.patch(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[item],
    )
    assert response.status_code == 200, f"PATCH failed: {response.text}"
    bulk = response.json()
    assert bulk["total"] == 1
    return bulk, bulk["results"][0]


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_creates_new_version(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """A simple PATCH bumps the version and preserves document_id and identity_hash."""
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)
    assert initial["version"] == 1

    _, result = await patch_one(
        client, auth_headers, initial["document_id"], {"first_name": "Jane"}
    )
    assert result["status"] == "updated"
    assert result["version"] == 2
    assert result["document_id"] == initial["document_id"]
    assert result["identity_hash"] == initial["identity_hash"]
    assert result["is_new"] is False

    # Verify the merged data is correct via GET
    resp = await client.get(
        f"/api/document-store/documents/{initial['document_id']}",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["version"] == 2
    assert doc["status"] == "active"
    assert doc["data"]["first_name"] == "Jane"
    # Untouched fields preserved
    assert doc["data"]["last_name"] == sample_person_data["last_name"]
    assert doc["data"]["national_id"] == sample_person_data["national_id"]


@pytest.mark.asyncio
async def test_patch_old_version_becomes_inactive(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """After PATCH, version 1 is INACTIVE and version 2 is ACTIVE."""
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)
    document_id = initial["document_id"]

    await patch_one(client, auth_headers, document_id, {"first_name": "Jane"})

    v1 = await client.get(
        f"/api/document-store/documents/{document_id}/versions/1",
        headers=auth_headers,
    )
    assert v1.status_code == 200
    assert v1.json()["status"] == "inactive"

    v2 = await client.get(
        f"/api/document-store/documents/{document_id}/versions/2",
        headers=auth_headers,
    )
    assert v2.status_code == 200
    assert v2.json()["status"] == "active"


@pytest.mark.asyncio
async def test_patch_preserves_template_version(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """The new version uses the same template_version as the existing document."""
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)

    await patch_one(client, auth_headers, initial["document_id"], {"first_name": "Jane"})

    resp = await client.get(
        f"/api/document-store/documents/{initial['document_id']}",
        headers=auth_headers,
    )
    new = resp.json()

    v1 = await client.get(
        f"/api/document-store/documents/{initial['document_id']}/versions/1",
        headers=auth_headers,
    )
    assert new["template_version"] == v1.json()["template_version"]


@pytest.mark.asyncio
async def test_patch_no_identity_template(
    client: AsyncClient, auth_headers: dict
):
    """A template with no identity fields can still be patched."""
    initial = await create_one(
        client,
        auth_headers,
        "NO_IDENTITY",
        {"title": "Original", "notes": "first"},
    )
    assert initial["version"] == 1

    _, result = await patch_one(
        client, auth_headers, initial["document_id"], {"notes": "updated"}
    )
    assert result["status"] == "updated"
    assert result["version"] == 2

    resp = await client.get(
        f"/api/document-store/documents/{initial['document_id']}",
        headers=auth_headers,
    )
    doc = resp.json()
    assert doc["data"]["title"] == "Original"
    assert doc["data"]["notes"] == "updated"


@pytest.mark.asyncio
async def test_patch_employee_non_identity_field(
    client: AsyncClient, auth_headers: dict, sample_employee_data: dict
):
    """Patching a non-identity field on EMPLOYEE works."""
    initial = await create_one(client, auth_headers, "EMPLOYEE", sample_employee_data)

    _, result = await patch_one(
        client,
        auth_headers,
        initial["document_id"],
        {"department": "Sales", "manager_id": "MGR002"},
    )
    assert result["status"] == "updated"
    assert result["version"] == 2

    resp = await client.get(
        f"/api/document-store/documents/{initial['document_id']}",
        headers=auth_headers,
    )
    doc = resp.json()
    assert doc["data"]["department"] == "Sales"
    assert doc["data"]["manager_id"] == "MGR002"
    # Identity fields untouched
    assert doc["data"]["employee_id"] == sample_employee_data["employee_id"]
    assert doc["data"]["company_id"] == sample_employee_data["company_id"]


# ---------------------------------------------------------------------------
# RFC 7396 merge semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_null_deletes_optional_field(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """A null value in the patch deletes the field."""
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)

    _, result = await patch_one(
        client, auth_headers, initial["document_id"], {"birth_date": None}
    )
    assert result["status"] == "updated"

    resp = await client.get(
        f"/api/document-store/documents/{initial['document_id']}",
        headers=auth_headers,
    )
    doc = resp.json()
    assert "birth_date" not in doc["data"]


# Array-replace semantics are exercised by the json_merge_patch unit tests
# below; the test fixture templates don't define array-typed fields.


# ---------------------------------------------------------------------------
# No-op detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_empty_body_is_noop(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """An empty patch produces no new version."""
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)

    _, result = await patch_one(client, auth_headers, initial["document_id"], {})
    assert result["status"] == "unchanged"
    assert result["version"] == 1
    assert result["is_new"] is False

    # Confirm no v2 exists
    resp = await client.get(
        f"/api/document-store/documents/{initial['document_id']}/versions",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["current_version"] == 1


@pytest.mark.asyncio
async def test_patch_same_value_is_noop(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """Patching a field to its current value produces no new version."""
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)

    _, result = await patch_one(
        client,
        auth_headers,
        initial["document_id"],
        {"first_name": sample_person_data["first_name"]},
    )
    assert result["status"] == "unchanged"
    assert result["version"] == 1


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_not_found(client: AsyncClient, auth_headers: dict):
    """Patching a nonexistent document returns not_found per item."""
    _, result = await patch_one(
        client, auth_headers, "nonexistent-doc-id", {"first_name": "Jane"}
    )
    assert result["status"] == "error"
    assert result["error_code"] == "not_found"


@pytest.mark.asyncio
async def test_patch_identity_field_change_rejected(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """Patching an identity field returns identity_field_change."""
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)

    _, result = await patch_one(
        client, auth_headers, initial["document_id"], {"national_id": "999999999"}
    )
    assert result["status"] == "error"
    assert result["error_code"] == "identity_field_change"
    assert "national_id" in result["error"]


@pytest.mark.asyncio
async def test_patch_archived_document_rejected(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """Patching an archived document returns the 'archived' error code."""
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)

    archive_resp = await client.post(
        "/api/document-store/documents/archive",
        headers=auth_headers,
        json=[{"id": initial["document_id"]}],
    )
    assert archive_resp.status_code == 200
    assert archive_resp.json()["succeeded"] == 1

    _, result = await patch_one(
        client, auth_headers, initial["document_id"], {"first_name": "Jane"}
    )
    assert result["status"] == "error"
    assert result["error_code"] == "archived"


@pytest.mark.asyncio
async def test_patch_concurrency_conflict_on_if_match_mismatch(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """if_match that doesn't equal the current version returns concurrency_conflict."""
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)
    assert initial["version"] == 1

    _, result = await patch_one(
        client, auth_headers, initial["document_id"], {"first_name": "Jane"}, if_match=99
    )
    assert result["status"] == "error"
    assert result["error_code"] == "concurrency_conflict"
    assert "99" in result["error"]


@pytest.mark.asyncio
async def test_patch_if_match_success(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """if_match equal to the current version succeeds."""
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)

    _, result = await patch_one(
        client, auth_headers, initial["document_id"], {"first_name": "Jane"}, if_match=1
    )
    assert result["status"] == "updated"
    assert result["version"] == 2


@pytest.mark.asyncio
async def test_patch_validation_failed(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """A patch that breaks template validation returns validation_failed."""
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)

    # email field has a regex pattern; an invalid email triggers validation_failed
    _, result = await patch_one(
        client, auth_headers, initial["document_id"], {"email": "not-an-email"}
    )
    assert result["status"] == "error"
    assert result["error_code"] == "validation_failed"


@pytest.mark.asyncio
async def test_patch_required_field_deletion_fails_validation(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """Deleting a required field via null fails template validation."""
    initial = await create_one(client, auth_headers, "PERSON", sample_person_data)

    _, result = await patch_one(
        client, auth_headers, initial["document_id"], {"first_name": None}
    )
    assert result["status"] == "error"
    assert result["error_code"] == "validation_failed"


# ---------------------------------------------------------------------------
# Bulk semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_bulk_mixed_success_and_error(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """A bulk PATCH with one success and one failure returns both per-item."""
    a = await create_one(client, auth_headers, "PERSON", sample_person_data)
    second_data = sample_person_data.copy()
    second_data["national_id"] = "987654321"
    b = await create_one(client, auth_headers, "PERSON", second_data)

    response = await client.patch(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[
            {"document_id": a["document_id"], "patch": {"first_name": "Updated"}},
            {"document_id": b["document_id"], "patch": {"national_id": "111111111"}},
            {"document_id": "nonexistent", "patch": {"first_name": "X"}},
        ],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["total"] == 3
    assert bulk["succeeded"] == 1
    assert bulk["failed"] == 2

    # Item 0: success
    assert bulk["results"][0]["status"] == "updated"
    assert bulk["results"][0]["index"] == 0
    # Item 1: identity field change error
    assert bulk["results"][1]["status"] == "error"
    assert bulk["results"][1]["error_code"] == "identity_field_change"
    assert bulk["results"][1]["index"] == 1
    # Item 2: not_found
    assert bulk["results"][2]["status"] == "error"
    assert bulk["results"][2]["error_code"] == "not_found"
    assert bulk["results"][2]["index"] == 2


@pytest.mark.asyncio
async def test_patch_preserves_metadata_custom(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """Custom metadata is carried forward to the new version on PATCH."""
    initial_response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{
            "namespace": "wip",
            "template_id": "PERSON",
            "data": sample_person_data,
            "metadata": {"source": "test", "tag": "alpha"},
        }],
    )
    assert initial_response.status_code == 200
    document_id = initial_response.json()["results"][0]["document_id"]

    await patch_one(client, auth_headers, document_id, {"first_name": "Jane"})

    resp = await client.get(
        f"/api/document-store/documents/{document_id}",
        headers=auth_headers,
    )
    doc = resp.json()
    assert doc["metadata"]["custom"] == {"source": "test", "tag": "alpha"}


# ---------------------------------------------------------------------------
# json_merge_patch unit tests (RFC 7396 contract)
# ---------------------------------------------------------------------------


def test_json_merge_patch_scalar_replace():
    from document_store.services.document_service import json_merge_patch

    assert json_merge_patch({"a": 1}, {"a": 2}) == {"a": 2}


def test_json_merge_patch_deep_merge():
    from document_store.services.document_service import json_merge_patch

    target = {"a": {"b": 1, "c": 2}, "d": 3}
    patch = {"a": {"c": 99, "e": 4}}
    assert json_merge_patch(target, patch) == {
        "a": {"b": 1, "c": 99, "e": 4},
        "d": 3,
    }


def test_json_merge_patch_array_replace():
    from document_store.services.document_service import json_merge_patch

    target = {"tags": ["a", "b"]}
    patch = {"tags": ["x", "y", "z"]}
    assert json_merge_patch(target, patch) == {"tags": ["x", "y", "z"]}


def test_json_merge_patch_null_deletes():
    from document_store.services.document_service import json_merge_patch

    target = {"a": 1, "b": 2}
    patch = {"a": None}
    assert json_merge_patch(target, patch) == {"b": 2}


def test_json_merge_patch_null_on_missing_key_is_noop():
    from document_store.services.document_service import json_merge_patch

    target = {"a": 1}
    patch = {"b": None}
    assert json_merge_patch(target, patch) == {"a": 1}


def test_json_merge_patch_dict_on_non_dict_replaces():
    from document_store.services.document_service import json_merge_patch

    target = {"a": "scalar"}
    patch = {"a": {"nested": True}}
    assert json_merge_patch(target, patch) == {"a": {"nested": True}}


def test_json_merge_patch_empty_patch():
    from document_store.services.document_service import json_merge_patch

    target = {"a": 1, "b": {"c": 2}}
    assert json_merge_patch(target, {}) == target


def test_json_merge_patch_does_not_mutate_input():
    from document_store.services.document_service import json_merge_patch

    target = {"a": 1, "b": {"c": 2}}
    patch = {"a": 99, "b": {"d": 3}}
    json_merge_patch(target, patch)
    assert target == {"a": 1, "b": {"c": 2}}
