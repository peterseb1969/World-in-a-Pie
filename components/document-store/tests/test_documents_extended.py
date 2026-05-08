"""Extended tests for document operations.

Covers:
- Get latest document version
- Complex queries with multiple filters
- Pagination edge cases
- Bulk archive operations
- Document versioning scenarios
"""

import pytest
from httpx import AsyncClient


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
# Tests: Get Latest Document Version
# ============================================================================

@pytest.mark.asyncio
async def test_get_latest_version_single_version(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Get latest version when only one version exists."""
    result = await create_one(client, auth_headers, "PERSON", sample_person_data)
    doc_id = result["document_id"]

    response = await client.get(
        f"/api/document-store/documents/{doc_id}/latest",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["document_id"] == doc_id
    assert data["version"] == 1


@pytest.mark.asyncio
async def test_get_latest_version_multiple_versions(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Get latest version when multiple versions exist (via identity-based upsert)."""
    # Create initial document
    result = await create_one(client, auth_headers, "PERSON", sample_person_data)
    doc_id = result["document_id"]
    assert result["version"] == 1

    # Create second version by submitting same identity with updated data
    updated_data = sample_person_data.copy()
    updated_data["first_name"] = "Jane"
    updated_data["age"] = 35
    result2 = await create_one(client, auth_headers, "PERSON", updated_data)
    assert result2["document_id"] == doc_id  # Same identity -> same document_id
    assert result2["version"] == 2

    # Get latest version
    response = await client.get(
        f"/api/document-store/documents/{doc_id}/latest",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["document_id"] == doc_id
    assert data["version"] == 2
    assert data["data"]["first_name"] == "Jane"
    assert data["data"]["age"] == 35


@pytest.mark.asyncio
async def test_get_latest_version_not_found(client: AsyncClient, auth_headers: dict):
    """Get latest version for non-existent document returns 404."""
    response = await client.get(
        "/api/document-store/documents/nonexistent-id/latest",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_specific_version(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Get a specific (older) version of a document."""
    # Create initial document
    result = await create_one(client, auth_headers, "PERSON", sample_person_data)
    doc_id = result["document_id"]

    # Create second version
    updated_data = sample_person_data.copy()
    updated_data["first_name"] = "Updated"
    await create_one(client, auth_headers, "PERSON", updated_data)

    # Get version 1 specifically
    response = await client.get(
        f"/api/document-store/documents/{doc_id}/versions/1",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == 1
    assert data["data"]["first_name"] == sample_person_data["first_name"]


@pytest.mark.asyncio
async def test_get_version_history(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Get the full version history for a document."""
    # Create 3 versions
    result = await create_one(client, auth_headers, "PERSON", sample_person_data)
    doc_id = result["document_id"]

    for i in range(2):
        updated = sample_person_data.copy()
        updated["first_name"] = f"Version{i + 2}"
        await create_one(client, auth_headers, "PERSON", updated)

    response = await client.get(
        f"/api/document-store/documents/{doc_id}/versions",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["current_version"] == 3
    assert len(data["versions"]) == 3
    versions = sorted(data["versions"], key=lambda v: v["version"])
    assert versions[0]["version"] == 1
    assert versions[1]["version"] == 2
    assert versions[2]["version"] == 3


# ============================================================================
# Tests: Complex Query with Multiple Filters
# ============================================================================

@pytest.mark.asyncio
async def test_query_multiple_filters(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Query documents with multiple filters (AND logic)."""
    # Create documents with varied data
    people = [
        {"national_id": "100000001", "first_name": "Alice", "last_name": "Smith", "age": 25},
        {"national_id": "100000002", "first_name": "Bob", "last_name": "Smith", "age": 35},
        {"national_id": "100000003", "first_name": "Charlie", "last_name": "Jones", "age": 25},
        {"national_id": "100000004", "first_name": "Diana", "last_name": "Smith", "age": 45},
    ]
    for person in people:
        await create_one(client, auth_headers, "PERSON", person)

    # Query: last_name == "Smith" AND age >= 30
    response = await client.post(
        "/api/document-store/documents/query",
        headers=auth_headers,
        json={
            "filters": [
                {"field": "data.last_name", "operator": "eq", "value": "Smith"},
                {"field": "data.age", "operator": "gte", "value": 30},
            ],
            "template_id": "PERSON",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2  # Bob (35) and Diana (45)
    names = {item["data"]["first_name"] for item in data["items"]}
    assert names == {"Bob", "Diana"}


@pytest.mark.asyncio
async def test_query_with_in_operator(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Query using the 'in' operator for matching multiple values."""
    people = [
        {"national_id": "200000001", "first_name": "Alice", "last_name": "Doe", "age": 20},
        {"national_id": "200000002", "first_name": "Bob", "last_name": "Doe", "age": 30},
        {"national_id": "200000003", "first_name": "Charlie", "last_name": "Doe", "age": 40},
    ]
    for person in people:
        await create_one(client, auth_headers, "PERSON", person)

    response = await client.post(
        "/api/document-store/documents/query",
        headers=auth_headers,
        json={
            "filters": [
                {"field": "data.first_name", "operator": "in", "value": ["Alice", "Charlie"]},
            ],
            "template_id": "PERSON",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    names = {item["data"]["first_name"] for item in data["items"]}
    assert names == {"Alice", "Charlie"}


@pytest.mark.asyncio
async def test_query_with_exists_operator(client: AsyncClient, auth_headers: dict):
    """Query using the 'exists' operator to find documents with/without a field."""
    # Create docs with and without optional email field
    person_with_email = {
        "national_id": "300000001",
        "first_name": "WithEmail",
        "last_name": "Test",
        "email": "test@example.com",
    }
    person_without_email = {
        "national_id": "300000002",
        "first_name": "NoEmail",
        "last_name": "Test",
    }
    await create_one(client, auth_headers, "PERSON", person_with_email)
    await create_one(client, auth_headers, "PERSON", person_without_email)

    # Query: email exists
    response = await client.post(
        "/api/document-store/documents/query",
        headers=auth_headers,
        json={
            "filters": [
                {"field": "data.email", "operator": "exists", "value": True},
            ],
            "template_id": "PERSON",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["data"]["first_name"] == "WithEmail"


@pytest.mark.asyncio
async def test_query_with_sorting(client: AsyncClient, auth_headers: dict):
    """Query documents with explicit sort order."""
    people = [
        {"national_id": "400000001", "first_name": "Zara", "last_name": "Test", "age": 30},
        {"national_id": "400000002", "first_name": "Alice", "last_name": "Test", "age": 25},
        {"national_id": "400000003", "first_name": "Mike", "last_name": "Test", "age": 40},
    ]
    for person in people:
        await create_one(client, auth_headers, "PERSON", person)

    # Sort by age ascending
    response = await client.post(
        "/api/document-store/documents/query",
        headers=auth_headers,
        json={
            "template_id": "PERSON",
            "sort_by": "data.age",
            "sort_order": "asc",
        },
    )
    assert response.status_code == 200
    data = response.json()
    ages = [item["data"]["age"] for item in data["items"]]
    assert ages == sorted(ages)


# ============================================================================
# Tests: GET /documents sort_by / sort_order (CASE-304)
# ============================================================================


@pytest.mark.asyncio
async def test_list_documents_sort_by_updated_at_desc(client: AsyncClient, auth_headers: dict):
    """GET /documents honours sort_by=updated_at + sort_order=desc."""
    for i in range(3):
        await create_one(
            client, auth_headers, "PERSON",
            {"national_id": f"55000000{i}", "first_name": f"Sort{i}", "last_name": "Test"},
        )

    response = await client.get(
        "/api/document-store/documents?sort_by=updated_at&sort_order=desc",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    timestamps = [item["updated_at"] for item in data["items"]]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_list_documents_sort_by_unknown_field_returns_422(client: AsyncClient, auth_headers: dict):
    """Unknown sort_by gets 422 with a hint listing supported fields."""
    response = await client.get(
        "/api/document-store/documents?sort_by=nope",
        headers=auth_headers,
    )
    assert response.status_code == 422
    detail = response.json().get("detail", "")
    assert "created_at" in detail
    assert "updated_at" in detail
    assert "data.<path>" in detail


@pytest.mark.asyncio
async def test_list_documents_sort_order_invalid_returns_422(client: AsyncClient, auth_headers: dict):
    """Invalid sort_order gets 422."""
    response = await client.get(
        "/api/document-store/documents?sort_by=created_at&sort_order=sideways",
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert "asc" in response.json()["detail"]


@pytest.mark.asyncio
async def test_list_documents_default_sort_unchanged(client: AsyncClient, auth_headers: dict):
    """Without sort_by/sort_order, behaviour matches pre-CASE-304 default (created_at desc)."""
    for i in range(3):
        await create_one(
            client, auth_headers, "PERSON",
            {"national_id": f"56000000{i}", "first_name": f"Default{i}", "last_name": "Test"},
        )

    response = await client.get(
        "/api/document-store/documents",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    timestamps = [item["created_at"] for item in data["items"]]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_list_documents_cursor_with_sort_returns_422(client: AsyncClient, auth_headers: dict):
    """Cursor pagination is _id-ordered; passing sort_by alongside is rejected."""
    response = await client.get(
        "/api/document-store/documents?cursor=507f1f77bcf86cd799439011&sort_by=updated_at",
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert "cursor" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_documents_latest_only_with_sort(client: AsyncClient, auth_headers: dict):
    """latest_only path applies sort_by/sort_order to the deduped result."""
    for i in range(3):
        await create_one(
            client, auth_headers, "PERSON",
            {"national_id": f"57000000{i}", "first_name": f"Latest{i}", "last_name": "Test"},
        )

    response = await client.get(
        "/api/document-store/documents?latest_only=true&sort_by=updated_at&sort_order=asc",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    timestamps = [item["updated_at"] for item in data["items"]]
    assert timestamps == sorted(timestamps)


# ============================================================================
# CASE-304 tiebreaker — pagination determinism on identical primary-sort
# ============================================================================


def test_build_sort_clauses_appends_document_id_tiebreaker():
    """Helper always appends ('document_id', 1) for stable pagination at ties."""
    from document_store.services.document_service import build_sort_clauses

    clauses = build_sort_clauses("updated_at", "desc")
    assert clauses == [("updated_at", -1), ("document_id", 1)]

    clauses_default = build_sort_clauses(None, None)
    assert clauses_default == [("created_at", -1), ("document_id", 1)]


def test_build_sort_clauses_accepts_data_path():
    """Helper accepts data.<path> for forward-compat with sortable_fields."""
    from document_store.services.document_service import build_sort_clauses

    clauses = build_sort_clauses("data.severity", "asc")
    assert clauses == [("data.severity", 1), ("document_id", 1)]


def test_build_sort_clauses_rejects_unknown_field():
    """Unknown sort field raises ValueError with a hint."""
    import pytest as _pytest

    from document_store.services.document_service import build_sort_clauses
    with _pytest.raises(ValueError, match="Unknown sort field"):
        build_sort_clauses("nope", "asc")


def test_build_sort_clauses_rejects_metadata_path_with_specific_message():
    """CASE-317: sort_by is a declarative slot (the platform commits
    to the field for pagination order). metadata.* is caller-attached
    context — must be rejected with a metadata-aware message that
    distinguishes from a typo or unknown field."""
    import pytest as _pytest

    from document_store.services.document_service import build_sort_clauses
    with _pytest.raises(ValueError) as excinfo:
        build_sort_clauses("metadata.custom.case_number", "asc")
    msg = str(excinfo.value)
    # The metadata-specific hint surfaces explicitly (not the generic
    # "Unknown sort field" path). Operators reaching for metadata.<x>
    # in sort_by need to know POST /documents/query filters still work.
    assert "metadata path" in msg
    assert "metadata.custom.case_number" in msg
    assert "POST /documents/query" in msg


def test_build_sort_clauses_rejects_bare_metadata_prefix():
    """Any path starting with metadata.<x> hits the same hint, not
    just deeply-nested metadata.custom.<x>."""
    import pytest as _pytest

    from document_store.services.document_service import build_sort_clauses
    with _pytest.raises(ValueError, match="metadata path"):
        build_sort_clauses("metadata.source_system", "desc")


def test_build_sort_clauses_rejects_invalid_order():
    """Invalid sort_order raises ValueError."""
    import pytest as _pytest

    from document_store.services.document_service import build_sort_clauses
    with _pytest.raises(ValueError, match="Invalid sort_order"):
        build_sort_clauses("created_at", "sideways")


@pytest.mark.asyncio
async def test_list_documents_pagination_deterministic_at_ties(client: AsyncClient, auth_headers: dict):
    """Two pages of size 1 over 3 docs return distinct documents — no doc appears on both pages.

    Regression guard for the live pagination scrambling APP-KB-YAC verified
    against wip-kb on 2026-05-08 (3 docs sharing identical microsecond updated_at).
    The document_id ASC tiebreaker keeps page boundaries stable.
    """
    for i in range(3):
        await create_one(
            client, auth_headers, "PERSON",
            {"national_id": f"58000000{i}", "first_name": f"Tie{i}", "last_name": "Test"},
        )

    response_p1 = await client.get(
        "/api/document-store/documents?sort_by=updated_at&sort_order=desc&page=1&page_size=1",
        headers=auth_headers,
    )
    response_p2 = await client.get(
        "/api/document-store/documents?sort_by=updated_at&sort_order=desc&page=2&page_size=1",
        headers=auth_headers,
    )
    assert response_p1.status_code == 200
    assert response_p2.status_code == 200

    p1_id = response_p1.json()["items"][0]["document_id"]
    p2_id = response_p2.json()["items"][0]["document_id"]
    assert p1_id != p2_id, (
        "page 1 and page 2 returned the same document — pagination is non-deterministic "
        "(missing tiebreaker). CASE-304 regression."
    )


@pytest.mark.asyncio
async def test_query_cross_template(client: AsyncClient, auth_headers: dict, sample_person_data: dict, sample_employee_data: dict):
    """Query without template_id searches across all templates."""
    await create_one(client, auth_headers, "PERSON", sample_person_data)
    await create_one(client, auth_headers, "EMPLOYEE", sample_employee_data)

    response = await client.post(
        "/api/document-store/documents/query",
        headers=auth_headers,
        json={
            "filters": [],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    template_ids = {item["template_id"] for item in data["items"]}
    assert len(template_ids) == 2  # two distinct templates


# ============================================================================
# Tests: Pagination Edge Cases
# ============================================================================

@pytest.mark.asyncio
async def test_pagination_page_beyond_total(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Requesting a page beyond the total returns empty items."""
    # Create 3 documents
    for i in range(3):
        data = sample_person_data.copy()
        data["national_id"] = f"50000000{i}"
        await create_one(client, auth_headers, "PERSON", data)

    # Request page 10 with page_size=2 (total=3, pages=2)
    response = await client.get(
        "/api/document-store/documents?template_id=PERSON&page=10&page_size=2",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["items"] == []
    assert data["page"] == 10


@pytest.mark.asyncio
async def test_pagination_single_item_pages(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Pagination with page_size=1 returns one document per page."""
    for i in range(3):
        data = sample_person_data.copy()
        data["national_id"] = f"60000000{i}"
        await create_one(client, auth_headers, "PERSON", data)

    response = await client.get(
        "/api/document-store/documents?template_id=PERSON&page=1&page_size=1",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 1
    assert data["pages"] == 3


@pytest.mark.asyncio
async def test_pagination_max_page_size(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Pagination with max page_size returns all documents in one page."""
    for i in range(3):
        data = sample_person_data.copy()
        data["national_id"] = f"70000000{i}"
        await create_one(client, auth_headers, "PERSON", data)

    response = await client.get(
        "/api/document-store/documents?template_id=PERSON&page=1&page_size=100",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3
    assert data["pages"] == 1


@pytest.mark.asyncio
async def test_pagination_empty_collection(client: AsyncClient, auth_headers: dict):
    """Pagination on empty collection returns zeros."""
    response = await client.get(
        "/api/document-store/documents?template_id=PERSON&page=1&page_size=20",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_query_pagination(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Query endpoint supports pagination."""
    for i in range(5):
        data = sample_person_data.copy()
        data["national_id"] = f"80000000{i}"
        data["age"] = 20 + i
        await create_one(client, auth_headers, "PERSON", data)

    response = await client.post(
        "/api/document-store/documents/query",
        headers=auth_headers,
        json={
            "template_id": "PERSON",
            "page": 1,
            "page_size": 2,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["page_size"] == 2


# ============================================================================
# Tests: Archive Multiple Documents in One Call
# ============================================================================

@pytest.mark.asyncio
async def test_archive_multiple_documents(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Archive multiple documents in a single bulk call."""
    doc_ids = []
    for i in range(3):
        data = sample_person_data.copy()
        data["national_id"] = f"90000000{i}"
        result = await create_one(client, auth_headers, "PERSON", data)
        doc_ids.append(result["document_id"])

    # Archive all 3 at once
    response = await client.post(
        "/api/document-store/documents/archive",
        headers=auth_headers,
        json=[{"id": doc_id} for doc_id in doc_ids],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["total"] == 3
    assert bulk["succeeded"] == 3
    assert bulk["failed"] == 0

    # Verify all are archived
    for doc_id in doc_ids:
        get_resp = await client.get(
            f"/api/document-store/documents/{doc_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_archive_nonexistent_document(client: AsyncClient, auth_headers: dict):
    """Archive a non-existent document returns error in bulk response."""
    response = await client.post(
        "/api/document-store/documents/archive",
        headers=auth_headers,
        json=[{"id": "nonexistent-doc-id"}],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["failed"] == 1
    assert "not found" in bulk["results"][0]["error"].lower()


@pytest.mark.asyncio
async def test_archive_mixed_results(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Archive with mixed success/failure results."""
    result = await create_one(client, auth_headers, "PERSON", sample_person_data)
    valid_id = result["document_id"]

    response = await client.post(
        "/api/document-store/documents/archive",
        headers=auth_headers,
        json=[
            {"id": valid_id},
            {"id": "nonexistent-id"},
        ],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["total"] == 2
    assert bulk["succeeded"] == 1
    assert bulk["failed"] == 1


@pytest.mark.asyncio
async def test_archive_already_archived_document(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Archiving an already-archived document should be idempotent or return an error."""
    result = await create_one(client, auth_headers, "PERSON", sample_person_data)
    doc_id = result["document_id"]

    # Archive it
    await client.post(
        "/api/document-store/documents/archive",
        headers=auth_headers,
        json=[{"id": doc_id}],
    )

    # Archive again - service may treat this as success or error,
    # we just verify it does not crash
    response = await client.post(
        "/api/document-store/documents/archive",
        headers=auth_headers,
        json=[{"id": doc_id}],
    )
    assert response.status_code == 200
    # The result could be either succeeded or failed depending on implementation
    bulk = response.json()
    assert bulk["total"] == 1


# ============================================================================
# Tests: List Documents with Template Value Filter
# ============================================================================

@pytest.mark.asyncio
async def test_list_documents_filter_by_template_value(client: AsyncClient, auth_headers: dict, sample_person_data: dict, sample_employee_data: dict):
    """List documents filtered by template_value."""
    await create_one(client, auth_headers, "PERSON", sample_person_data)
    await create_one(client, auth_headers, "EMPLOYEE", sample_employee_data)

    # Filter by PERSON template_value
    response = await client.get(
        "/api/document-store/documents?template_value=PERSON",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["template_value"] == "PERSON"


# ============================================================================
# Tests: List Documents with Status Filter
# ============================================================================

@pytest.mark.asyncio
async def test_list_documents_filter_by_status(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """List documents filtered by status."""
    # Create two docs
    result1 = await create_one(client, auth_headers, "PERSON", sample_person_data)
    data2 = sample_person_data.copy()
    data2["national_id"] = "999999999"
    await create_one(client, auth_headers, "PERSON", data2)

    # Delete one
    await client.request(
        "DELETE",
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{"id": result1["document_id"]}],
    )

    # List active only
    response = await client.get(
        "/api/document-store/documents?status=active",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1

    # List inactive only
    response = await client.get(
        "/api/document-store/documents?status=inactive",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


# ============================================================================
# Tests: Bulk Delete Multiple Documents
# ============================================================================

@pytest.mark.asyncio
async def test_bulk_delete_documents(client: AsyncClient, auth_headers: dict, sample_person_data: dict):
    """Bulk delete multiple documents in one call."""
    doc_ids = []
    for i in range(3):
        data = sample_person_data.copy()
        data["national_id"] = f"11000000{i}"
        result = await create_one(client, auth_headers, "PERSON", data)
        doc_ids.append(result["document_id"])

    response = await client.request(
        "DELETE",
        "/api/document-store/documents",
        headers=auth_headers,
        json=[{"id": doc_id} for doc_id in doc_ids],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["total"] == 3
    assert bulk["succeeded"] == 3

    # Verify all are inactive
    for doc_id in doc_ids:
        get_resp = await client.get(
            f"/api/document-store/documents/{doc_id}",
            headers=auth_headers,
        )
        assert get_resp.json()["status"] == "inactive"
