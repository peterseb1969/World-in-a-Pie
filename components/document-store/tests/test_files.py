"""Tests for file operations (upload, list, metadata, delete, orphans, integrity)."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.main import app
from document_store.models.document import Document
from document_store.models.file import File, FileMetadata, FileStatus

# Re-use conftest helpers — real Registry, no mock registry
from tests.conftest import (
    create_mock_def_store_client,
    create_mock_template_store_client,
    setup_registry_and_app,
)
from wip_auth.resolve import clear_resolution_cache, set_resolve_transport

# Module-level registry transport — set by file_client fixture so _insert_file
# can register files in Registry (resolution requires Registry entries).
_registry_transport = None


# ---------------------------------------------------------------------------
# Helper: create a File document in MongoDB + register in Registry
# ---------------------------------------------------------------------------
async def _register_file_in_registry(namespace: str, checksum: str) -> str:
    """Register a file entry in Registry and return the UUID7 file_id.

    Mirrors what FileService._generate_file_id does in production:
    empty composite_key, Registry assigns UUID7 ID.
    """
    api_key = os.environ["MASTER_API_KEY"]
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    async with AsyncClient(transport=_registry_transport, base_url="http://registry") as client:
        resp = await client.post(
            "/api/registry/entries/register",
            headers=headers,
            json=[{
                "namespace": namespace,
                "entity_type": "files",
                "composite_key": {},
                "metadata": {"type": "file", "checksum": checksum},
            }],
        )
        assert resp.status_code == 200, f"Register file failed: {resp.text}"
        result = resp.json()["results"][0]
        assert result["status"] in ("created", "exists"), f"Register file: {result}"
        return result["registry_id"]


async def _insert_file(
    filename: str = "test.pdf",
    content_type: str = "application/pdf",
    size_bytes: int = 1024,
    checksum: str | None = None,
    status: FileStatus = FileStatus.ORPHAN,
    reference_count: int = 0,
    namespace: str = "wip",
    tags: list[str] | None = None,
    category: str | None = None,
    description: str | None = None,
    allowed_templates: list[str] | None = None,
    uploaded_at: datetime | None = None,
    uploaded_by: str | None = None,
) -> File:
    """Register a file in Registry and insert into MongoDB.

    The file_id is a UUID7 assigned by Registry (same as production).
    """
    if checksum is None:
        checksum = uuid.uuid4().hex * 2  # 64 hex chars, like sha256
    if uploaded_at is None:
        uploaded_at = datetime.now(UTC)

    # Register in Registry — get UUID7 file_id (same as production)
    file_id = await _register_file_in_registry(namespace, checksum)

    file_doc = File(
        namespace=namespace,
        file_id=file_id,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        checksum=checksum,
        storage_key=file_id,
        metadata=FileMetadata(
            description=description,
            tags=tags or [],
            category=category,
        ),
        status=status,
        reference_count=reference_count,
        allowed_templates=allowed_templates,
        uploaded_at=uploaded_at,
        uploaded_by=uploaded_by,
    )
    await file_doc.insert()
    return file_doc


# ---------------------------------------------------------------------------
# Fixture: async HTTP client with file storage enabled + real Registry
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture(scope="function")
async def file_client():
    """Create an async HTTP client with file storage enabled and real Registry."""
    global _registry_transport
    mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"])
    real_registry, _registry_transport = await setup_registry_and_app(
        mongo_client, document_models=[Document, File]
    )

    # Also clean File collection (setup_registry_and_app cleans Document)
    await File.delete_all()

    # Mock Template-Store and Def-Store (separate services)
    mock_template_store = create_mock_template_store_client()
    mock_def_store = create_mock_def_store_client()

    # Mock file storage as enabled
    mock_storage = AsyncMock()
    mock_storage.upload = AsyncMock()
    mock_storage.download = AsyncMock(return_value=b"fake content")
    mock_storage.download_stream = MagicMock()
    mock_storage.delete = AsyncMock()
    mock_storage.exists = AsyncMock(return_value=True)
    mock_storage.generate_download_url = AsyncMock(return_value="https://minio.local/presigned-url")
    mock_storage.health_check = AsyncMock(return_value=True)
    mock_storage.ensure_bucket_exists = AsyncMock(return_value=True)

    # Mock NATS so publish calls don't fail
    mock_nats_publish = AsyncMock()

    with (
        patch('document_store.services.document_service.get_registry_client', return_value=real_registry),
        patch('document_store.services.document_service.get_template_store_client', return_value=mock_template_store),
        patch('document_store.services.document_service.get_def_store_client', return_value=mock_def_store),
        patch('document_store.services.validation_service.get_template_store_client', return_value=mock_template_store),
        patch('document_store.services.validation_service.get_def_store_client', return_value=mock_def_store),
        patch('document_store.main.get_registry_client', return_value=real_registry),
        patch('document_store.main.get_template_store_client', return_value=mock_template_store),
        patch('document_store.main.get_def_store_client', return_value=mock_def_store),
        patch('document_store.api.table_view.get_template_store_client', return_value=mock_template_store),
        patch('document_store.api.files.is_file_storage_enabled', return_value=True),
        patch('document_store.services.file_service.is_file_storage_enabled', return_value=True),
        patch('document_store.services.file_service.get_file_storage_client', return_value=mock_storage),
        patch('document_store.api.files.get_file_storage_client', return_value=mock_storage),
        patch('document_store.services.file_service.publish_file_event', mock_nats_publish),
    ):
        # Reset singleton so we get a fresh instance each test
        import document_store.services.file_service as fs_mod
        fs_mod._service = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

        # Clean up singleton
        fs_mod._service = None

    # Cleanup
    _registry_transport = None
    set_resolve_transport(None)
    clear_resolution_cache()


@pytest.fixture
def auth_headers() -> dict:
    """Return headers with API key for authenticated requests."""
    return {"X-API-Key": os.environ["API_KEY"]}


# ============================================================================
# Tests: List Files
# ============================================================================

@pytest.mark.asyncio
async def test_list_files_empty(file_client: AsyncClient, auth_headers: dict):
    """List files when none exist returns empty list."""
    response = await file_client.get(
        "/api/document-store/files",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []
    assert data["page"] == 1


@pytest.mark.asyncio
async def test_list_files_with_data(file_client: AsyncClient, auth_headers: dict):
    """List files returns inserted files."""
    await _insert_file(filename="doc1.pdf")
    await _insert_file(filename="doc2.png", content_type="image/png")

    response = await file_client.get(
        "/api/document-store/files",
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_list_files_filter_by_status(file_client: AsyncClient, auth_headers: dict):
    """List files filtered by status."""
    file_a = await _insert_file(status=FileStatus.ORPHAN)
    file_b = await _insert_file(status=FileStatus.ACTIVE, reference_count=1)
    await _insert_file(status=FileStatus.INACTIVE)

    # Filter: orphan only
    response = await file_client.get(
        "/api/document-store/files",
        headers=auth_headers,
        params={"namespace": "wip", "status": "orphan"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["file_id"] == file_a.file_id

    # Filter: active only
    response = await file_client.get(
        "/api/document-store/files",
        headers=auth_headers,
        params={"namespace": "wip", "status": "active"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["file_id"] == file_b.file_id


@pytest.mark.asyncio
async def test_list_files_filter_by_content_type(file_client: AsyncClient, auth_headers: dict):
    """List files filtered by content type (exact and wildcard)."""
    await _insert_file(content_type="image/png")
    await _insert_file(content_type="image/jpeg")
    await _insert_file(content_type="application/pdf")

    # Wildcard: image/*
    response = await file_client.get(
        "/api/document-store/files",
        headers=auth_headers,
        params={"namespace": "wip", "content_type": "image/*"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2

    # Exact: application/pdf
    response = await file_client.get(
        "/api/document-store/files",
        headers=auth_headers,
        params={"namespace": "wip", "content_type": "application/pdf"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_list_files_filter_by_category(file_client: AsyncClient, auth_headers: dict):
    """List files filtered by category."""
    await _insert_file(category="invoices")
    await _insert_file(category="receipts")
    await _insert_file(category="invoices")

    response = await file_client.get(
        "/api/document-store/files",
        headers=auth_headers,
        params={"namespace": "wip", "category": "invoices"},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 2


@pytest.mark.asyncio
async def test_list_files_filter_by_tags(file_client: AsyncClient, auth_headers: dict):
    """List files filtered by tags (all must match)."""
    file_both = await _insert_file(tags=["urgent", "2024"])
    await _insert_file(tags=["urgent"])
    await _insert_file(tags=["2024"])

    response = await file_client.get(
        "/api/document-store/files",
        headers=auth_headers,
        params={"namespace": "wip", "tags": "urgent,2024"},
    )
    assert response.status_code == 200
    # Only the file with BOTH tags
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["file_id"] == file_both.file_id


@pytest.mark.asyncio
async def test_list_files_pagination(file_client: AsyncClient, auth_headers: dict):
    """List files with pagination."""
    for _ in range(5):
        await _insert_file()

    response = await file_client.get(
        "/api/document-store/files",
        headers=auth_headers,
        params={"namespace": "wip", "page": 1, "page_size": 2},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert data["pages"] == 3


# ============================================================================
# Tests: Get File Metadata
# ============================================================================

@pytest.mark.asyncio
async def test_get_file_metadata(file_client: AsyncClient, auth_headers: dict):
    """Get file metadata by ID."""
    file = await _insert_file(
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=4096,
        description="Quarterly report",
        tags=["report", "Q1"],
        category="reports",
    )

    response = await file_client.get(
        f"/api/document-store/files/{file.file_id}",
        headers=auth_headers,
    )
    assert response.status_code == 200, f"Response: {response.json()}"
    data = response.json()
    assert data["file_id"] == file.file_id
    assert data["filename"] == "report.pdf"
    assert data["content_type"] == "application/pdf"
    assert data["size_bytes"] == 4096
    assert data["metadata"]["description"] == "Quarterly report"
    assert data["metadata"]["tags"] == ["report", "Q1"]
    assert data["metadata"]["category"] == "reports"
    assert data["status"] == "orphan"


@pytest.mark.asyncio
async def test_get_file_not_found(file_client: AsyncClient, auth_headers: dict):
    """Get non-existent file returns 404."""
    response = await file_client.get(
        "/api/document-store/files/FILE-NONEXISTENT",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_file_requires_auth(file_client: AsyncClient):
    """Get file without auth returns 401."""
    response = await file_client.get(
        "/api/document-store/files/FILE-001",
    )
    assert response.status_code == 401


# ============================================================================
# Tests: Upload File
# ============================================================================

@pytest.mark.asyncio
async def test_upload_file(file_client: AsyncClient, auth_headers: dict):
    """Upload a file via multipart form data."""
    # We need to mock the Registry call inside FileService._generate_file_id
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "results": [{"status": "created", "registry_id": "FILE-UPLOAD01"}]
    }

    with patch('document_store.services.file_service.httpx.AsyncClient') as MockHttpx:
        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)
        MockHttpx.return_value = mock_http_client

        response = await file_client.post(
            "/api/document-store/files",
            headers=auth_headers,
            files={"file": ("test.txt", b"Hello, World!", "text/plain")},
            data={
                "namespace": "wip",
                "description": "A test file",
                "tags": "test,upload",
                "category": "testing",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["file_id"] == "FILE-UPLOAD01"
    assert data["filename"] == "test.txt"
    assert data["content_type"] == "text/plain"
    assert data["size_bytes"] == len(b"Hello, World!")
    assert data["status"] == "orphan"
    assert data["metadata"]["description"] == "A test file"
    assert data["metadata"]["tags"] == ["test", "upload"]
    assert data["metadata"]["category"] == "testing"


@pytest.mark.asyncio
async def test_upload_empty_file(file_client: AsyncClient, auth_headers: dict):
    """Upload an empty file returns 400."""
    response = await file_client.post(
        "/api/document-store/files",
        headers=auth_headers,
        files={"file": ("empty.txt", b"", "text/plain")},
        data={"namespace": "wip"},
    )
    assert response.status_code == 400
    assert "Empty file" in response.json()["detail"]


# ============================================================================
# Tests: Update File Metadata (PATCH - bulk)
# ============================================================================

@pytest.mark.asyncio
async def test_update_file_metadata(file_client: AsyncClient, auth_headers: dict):
    """Update metadata for an existing file."""
    file = await _insert_file(description="Old description", tags=["old"])

    response = await file_client.patch(
        "/api/document-store/files",
        headers=auth_headers,
        json=[{
            "file_id": file.file_id,
            "description": "New description",
            "tags": ["new", "updated"],
            "category": "updated-category",
        }],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["total"] == 1
    assert bulk["succeeded"] == 1
    assert bulk["results"][0]["status"] == "updated"
    assert bulk["results"][0]["id"] == file.file_id

    # Verify the update took effect
    get_resp = await file_client.get(
        f"/api/document-store/files/{file.file_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["metadata"]["description"] == "New description"
    assert data["metadata"]["tags"] == ["new", "updated"]
    assert data["metadata"]["category"] == "updated-category"


@pytest.mark.asyncio
async def test_update_file_metadata_not_found(file_client: AsyncClient, auth_headers: dict):
    """Update metadata for a non-existent file returns error in bulk response."""
    response = await file_client.patch(
        "/api/document-store/files",
        headers=auth_headers,
        json=[{
            "file_id": "FILE-NOPE",
            "description": "Whatever",
        }],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["failed"] == 1
    assert bulk["results"][0]["status"] == "error"
    assert "not found" in bulk["results"][0]["error"].lower()


@pytest.mark.asyncio
async def test_update_file_metadata_bulk(file_client: AsyncClient, auth_headers: dict):
    """Update metadata for multiple files in one call."""
    file_a = await _insert_file()
    file_b = await _insert_file()

    response = await file_client.patch(
        "/api/document-store/files",
        headers=auth_headers,
        json=[
            {"file_id": file_a.file_id, "description": "Updated 1"},
            {"file_id": file_b.file_id, "description": "Updated 2"},
        ],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["total"] == 2
    assert bulk["succeeded"] == 2


@pytest.mark.asyncio
async def test_update_inactive_file_metadata_fails(file_client: AsyncClient, auth_headers: dict):
    """Cannot update metadata on a deleted (inactive) file."""
    file = await _insert_file(status=FileStatus.INACTIVE)

    response = await file_client.patch(
        "/api/document-store/files",
        headers=auth_headers,
        json=[{
            "file_id": file.file_id,
            "description": "Should fail",
        }],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["failed"] == 1
    assert "deleted" in bulk["results"][0]["error"].lower()


# ============================================================================
# Tests: Delete File (soft-delete, bulk)
# ============================================================================

@pytest.mark.asyncio
async def test_delete_file(file_client: AsyncClient, auth_headers: dict):
    """Soft-delete a file sets status to inactive."""
    file = await _insert_file()

    response = await file_client.request(
        "DELETE",
        "/api/document-store/files",
        headers=auth_headers,
        json=[{"id": file.file_id}],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["succeeded"] == 1
    assert bulk["results"][0]["status"] == "deleted"

    # Verify the file is now inactive
    get_resp = await file_client.get(
        f"/api/document-store/files/{file.file_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_delete_file_not_found(file_client: AsyncClient, auth_headers: dict):
    """Delete a non-existent file returns error in bulk response."""
    response = await file_client.request(
        "DELETE",
        "/api/document-store/files",
        headers=auth_headers,
        json=[{"id": "FILE-MISSING"}],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["failed"] == 1
    assert "not found" in bulk["results"][0]["error"].lower()


@pytest.mark.asyncio
async def test_delete_referenced_file_without_force(file_client: AsyncClient, auth_headers: dict):
    """Delete a referenced file without force=true returns error."""
    file = await _insert_file(status=FileStatus.ACTIVE, reference_count=2)

    response = await file_client.request(
        "DELETE",
        "/api/document-store/files",
        headers=auth_headers,
        json=[{"id": file.file_id, "force": False}],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["failed"] == 1
    assert "referenced" in bulk["results"][0]["error"].lower()


@pytest.mark.asyncio
async def test_delete_referenced_file_with_force(file_client: AsyncClient, auth_headers: dict):
    """Delete a referenced file with force=true succeeds."""
    file = await _insert_file(status=FileStatus.ACTIVE, reference_count=2)

    response = await file_client.request(
        "DELETE",
        "/api/document-store/files",
        headers=auth_headers,
        json=[{"id": file.file_id, "force": True}],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["succeeded"] == 1

    # Verify inactive
    get_resp = await file_client.get(
        f"/api/document-store/files/{file.file_id}",
        headers=auth_headers,
    )
    assert get_resp.json()["status"] == "inactive"


@pytest.mark.asyncio
async def test_delete_already_inactive_file(file_client: AsyncClient, auth_headers: dict):
    """Deleting an already-inactive file is idempotent (succeeds)."""
    file = await _insert_file(status=FileStatus.INACTIVE)

    response = await file_client.request(
        "DELETE",
        "/api/document-store/files",
        headers=auth_headers,
        json=[{"id": file.file_id}],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["succeeded"] == 1


@pytest.mark.asyncio
async def test_delete_multiple_files(file_client: AsyncClient, auth_headers: dict):
    """Bulk delete multiple files in one call."""
    file_a = await _insert_file()
    file_b = await _insert_file()
    file_c = await _insert_file()

    response = await file_client.request(
        "DELETE",
        "/api/document-store/files",
        headers=auth_headers,
        json=[
            {"id": file_a.file_id},
            {"id": file_b.file_id},
            {"id": file_c.file_id},
        ],
    )
    assert response.status_code == 200
    bulk = response.json()
    assert bulk["total"] == 3
    assert bulk["succeeded"] == 3


# ============================================================================
# Tests: Find Orphaned Files
# ============================================================================

@pytest.mark.asyncio
async def test_list_orphan_files(file_client: AsyncClient, auth_headers: dict):
    """Find orphan files (status=orphan, not referenced)."""
    file_orphan1 = await _insert_file(status=FileStatus.ORPHAN)
    file_orphan2 = await _insert_file(status=FileStatus.ORPHAN)
    await _insert_file(status=FileStatus.ACTIVE, reference_count=1)
    await _insert_file(status=FileStatus.INACTIVE)

    response = await file_client.get(
        "/api/document-store/files/orphans/list",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    orphan_ids = {item["file_id"] for item in data}
    assert orphan_ids == {file_orphan1.file_id, file_orphan2.file_id}


@pytest.mark.asyncio
async def test_list_orphan_files_with_age_filter(file_client: AsyncClient, auth_headers: dict):
    """Find orphan files older than a given number of hours."""
    old_time = datetime.now(UTC) - timedelta(hours=48)
    recent_time = datetime.now(UTC) - timedelta(hours=1)

    file_old = await _insert_file(status=FileStatus.ORPHAN, uploaded_at=old_time)
    await _insert_file(status=FileStatus.ORPHAN, uploaded_at=recent_time)

    response = await file_client.get(
        "/api/document-store/files/orphans/list?older_than_hours=24",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["file_id"] == file_old.file_id


@pytest.mark.asyncio
async def test_list_orphan_files_with_limit(file_client: AsyncClient, auth_headers: dict):
    """Orphan listing respects limit parameter."""
    for _ in range(5):
        await _insert_file(status=FileStatus.ORPHAN)

    response = await file_client.get(
        "/api/document-store/files/orphans/list?limit=3",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3


# ============================================================================
# Tests: Find Files by Checksum
# ============================================================================

@pytest.mark.asyncio
async def test_find_by_checksum(file_client: AsyncClient, auth_headers: dict):
    """Find files by SHA-256 checksum (duplicate detection)."""
    shared_checksum = "abc123def456" * 6  # fake but consistent
    file_dup1 = await _insert_file(checksum=shared_checksum)
    file_dup2 = await _insert_file(checksum=shared_checksum)
    await _insert_file(checksum="unique_checksum_value_0000000000")

    response = await file_client.get(
        f"/api/document-store/files/by-checksum/{shared_checksum}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    found_ids = {item["file_id"] for item in data}
    assert found_ids == {file_dup1.file_id, file_dup2.file_id}


@pytest.mark.asyncio
async def test_find_by_checksum_no_match(file_client: AsyncClient, auth_headers: dict):
    """Find by checksum returns empty list when no match."""
    response = await file_client.get(
        "/api/document-store/files/by-checksum/nonexistent_checksum_value",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data == []


# ============================================================================
# Tests: File Integrity Check
# ============================================================================

@pytest.mark.asyncio
async def test_integrity_check_healthy(file_client: AsyncClient, auth_headers: dict):
    """Integrity check on a healthy system with no issues."""
    await _insert_file(status=FileStatus.ACTIVE, reference_count=1)
    await _insert_file(status=FileStatus.ACTIVE, reference_count=2)

    response = await file_client.get(
        "/api/document-store/files/health/integrity",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["summary"]["orphan_file"] == 0
    assert data["summary"]["inactive_referenced"] == 0


@pytest.mark.asyncio
async def test_integrity_check_detects_orphans(file_client: AsyncClient, auth_headers: dict):
    """Integrity check detects orphan files older than 24 hours."""
    old_time = datetime.now(UTC) - timedelta(hours=48)
    file_orphan = await _insert_file(status=FileStatus.ORPHAN, uploaded_at=old_time)

    response = await file_client.get(
        "/api/document-store/files/health/integrity",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["orphan_file"] >= 1
    orphan_issues = [i for i in data["issues"] if i["type"] == "orphan_file"]
    assert len(orphan_issues) >= 1
    assert orphan_issues[0]["file_id"] == file_orphan.file_id


@pytest.mark.asyncio
async def test_integrity_check_detects_broken_references(file_client: AsyncClient, auth_headers: dict):
    """Integrity check detects inactive files still referenced by documents."""
    await _insert_file(
        status=FileStatus.INACTIVE,
        reference_count=3,
    )

    response = await file_client.get(
        "/api/document-store/files/health/integrity",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["summary"]["inactive_referenced"] >= 1
    broken_issues = [i for i in data["issues"] if i["type"] == "broken_reference"]
    assert len(broken_issues) >= 1
    assert broken_issues[0]["severity"] == "error"


# ============================================================================
# Tests: File Storage Disabled
# ============================================================================

@pytest.mark.asyncio
async def test_file_endpoints_503_when_storage_disabled(client: AsyncClient, auth_headers: dict):
    """File endpoints return 503 when file storage is not enabled."""
    # The regular `client` fixture from conftest does NOT enable file storage
    with patch('document_store.api.files.is_file_storage_enabled', return_value=False):
        response = await client.get(
            "/api/document-store/files",
            headers=auth_headers,
            params={"namespace": "wip"},
        )
        assert response.status_code == 503
        assert "not enabled" in response.json()["detail"].lower()


# ============================================================================
# Tests: Download URL
# ============================================================================

@pytest.mark.asyncio
async def test_get_download_url(file_client: AsyncClient, auth_headers: dict):
    """Get a pre-signed download URL for a file."""
    file = await _insert_file(filename="download.pdf")

    response = await file_client.get(
        f"/api/document-store/files/{file.file_id}/download",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["file_id"] == file.file_id
    assert data["filename"] == "download.pdf"
    assert "download_url" in data
    assert data["expires_in"] == 3600  # default


@pytest.mark.asyncio
async def test_get_download_url_not_found(file_client: AsyncClient, auth_headers: dict):
    """Download URL for non-existent file returns 404."""
    response = await file_client.get(
        "/api/document-store/files/FILE-NOPE/download",
        headers=auth_headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_download_url_custom_expiry(file_client: AsyncClient, auth_headers: dict):
    """Download URL with custom expiry time."""
    file = await _insert_file()

    response = await file_client.get(
        f"/api/document-store/files/{file.file_id}/download?expires_in=7200",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["expires_in"] == 7200


# ============================================================================
# Tests: Hard Delete
# ============================================================================

@pytest.mark.asyncio
async def test_hard_delete_inactive_file(file_client: AsyncClient, auth_headers: dict):
    """Hard-delete an inactive file permanently removes it."""
    file = await _insert_file(status=FileStatus.INACTIVE)

    response = await file_client.delete(
        f"/api/document-store/files/{file.file_id}/hard",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "permanently_deleted"

    # Verify it no longer exists
    get_resp = await file_client.get(
        f"/api/document-store/files/{file.file_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_hard_delete_active_file_fails(file_client: AsyncClient, auth_headers: dict):
    """Hard-delete on an active file returns 400 (must soft-delete first)."""
    file = await _insert_file(status=FileStatus.ACTIVE, reference_count=1)

    response = await file_client.delete(
        f"/api/document-store/files/{file.file_id}/hard",
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "inactive" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_hard_delete_not_found(file_client: AsyncClient, auth_headers: dict):
    """Hard-delete on a non-existent file returns 404."""
    response = await file_client.delete(
        "/api/document-store/files/FILE-GHOST/hard",
        headers=auth_headers,
    )
    assert response.status_code == 404


# ============================================================================
# Tests: Documents Referencing a File
# ============================================================================

@pytest.mark.asyncio
async def test_get_file_documents_no_references(file_client: AsyncClient, auth_headers: dict):
    """List documents referencing a file when none exist."""
    file = await _insert_file()

    response = await file_client.get(
        f"/api/document-store/files/{file.file_id}/documents",
        headers=auth_headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_get_file_documents_not_found(file_client: AsyncClient, auth_headers: dict):
    """List documents referencing a non-existent file returns 404."""
    response = await file_client.get(
        "/api/document-store/files/FILE-NOPE/documents",
        headers=auth_headers,
    )
    assert response.status_code == 404
