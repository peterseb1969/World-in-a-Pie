"""File API endpoints for binary file storage."""

import asyncio
import math

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from wip_auth import check_namespace_permission, get_current_identity

from ..models.api_models import (
    BulkResponse,
    BulkResultItem,
    DeleteItem,
    FileDownloadResponse,
    FileIntegrityResponse,
    FileListResponse,
    FileResponse,
    FileUploadMetadata,
    UpdateFileItem,
)
from ..models.file import FileStatus
from ..services.file_service import FileServiceError, get_file_service
from ..services.file_storage_client import (
    get_file_storage_client,
    is_file_storage_enabled,
)
from ..services.nats_client import get_throttle_delay
from .auth import require_api_key


class FileDocumentRef(BaseModel):
    """A document that references a file."""
    document_id: str
    template_id: str
    template_value: str | None = None
    field_path: str
    status: str
    created_at: str | None = None


class FileDocumentsResponse(BaseModel):
    """Paginated list of documents referencing a file."""
    items: list[FileDocumentRef] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 10
    pages: int = 1

router = APIRouter(prefix="/files", tags=["Files"])


def require_file_storage():
    """Dependency to ensure file storage is enabled."""
    if not is_file_storage_enabled():
        raise HTTPException(
            status_code=503,
            detail="File storage is not enabled. Set WIP_FILE_STORAGE_ENABLED=true to enable."
        )


@router.post(
    "",
    response_model=FileResponse,
    summary="Upload a file",
    description="""
Upload a file to storage.

The file receives a unique ID from the Registry and is stored in MinIO.
Initially the file has status 'orphan' until it's referenced by a document.

Metadata can be provided as form fields:
- description: Human-readable description
- tags: Comma-separated tags (e.g., "invoice,2024,important")
- category: Classification category
- allowed_templates: Comma-separated template values that can use this file
    """,
    dependencies=[Depends(require_file_storage)]
)
async def upload_file(
    file: UploadFile = File(..., description="The file to upload"),
    namespace: str = Form(..., description="Namespace for the file"),
    file_id: str | None = Form(None, description="Pre-assigned file ID (for restore/migration)"),
    description: str | None = Form(None, description="File description"),
    tags: str | None = Form(None, description="Comma-separated tags"),
    category: str | None = Form(None, description="Classification category"),
    allowed_templates: str | None = Form(None, description="Comma-separated template values"),
    _: str = Depends(require_api_key)
):
    """Upload a file to storage."""
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "write")

    service = get_file_service()

    # Read file content with size limit to prevent OOM on resource-constrained devices
    from ..main import settings as app_settings
    max_size = app_settings.MAX_UPLOAD_SIZE
    content = bytearray()
    while True:
        chunk = await file.read(65536)  # 64KB chunks
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_size:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum upload size is {max_size // (1024 * 1024)}MB"
            )
    content = bytes(content)
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # Validate content type against allowlist (H5 — prevent malware uploads)
    from ..services.file_validation import validate_upload_content_type
    validate_upload_content_type(file.content_type or "application/octet-stream", file.filename or "unnamed")

    # Parse metadata from form fields
    metadata = FileUploadMetadata(
        description=description,
        tags=[t.strip() for t in tags.split(",")] if tags else [],
        category=category,
        allowed_templates=[t.strip() for t in allowed_templates.split(",")] if allowed_templates else None,
    )

    try:
        result = await service.upload_file(
            content=content,
            filename=file.filename or "unnamed",
            content_type=file.content_type or "application/octet-stream",
            metadata=metadata,
            namespace=namespace,
            file_id=file_id,
        )
        await asyncio.sleep(get_throttle_delay())
        return result
    except FileServiceError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get(
    "",
    response_model=FileListResponse,
    summary="List files",
    description="List files with optional filtering and pagination.",
    dependencies=[Depends(require_file_storage)]
)
async def list_files(
    namespace: str = Query(..., description="Namespace to query"),
    status: FileStatus | None = Query(None, description="Filter by status"),
    content_type: str | None = Query(None, description="Filter by MIME type (e.g., 'image/*')"),
    category: str | None = Query(None, description="Filter by category"),
    tags: str | None = Query(None, description="Comma-separated tags (all must match)"),
    uploaded_by: str | None = Query(None, description="Filter by uploader"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    _: str = Depends(require_api_key)
):
    """List files with pagination."""
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "read")

    service = get_file_service()

    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    return await service.list_files(
        status=status,
        content_type=content_type,
        category=category,
        tags=tag_list,
        uploaded_by=uploaded_by,
        page=page,
        page_size=page_size,
        namespace=namespace,
    )


@router.get(
    "/{file_id}",
    response_model=FileResponse,
    summary="Get file metadata",
    description="Retrieve file metadata by its unique ID.",
    dependencies=[Depends(require_file_storage)]
)
async def get_file(
    file_id: str,
    _: str = Depends(require_api_key)
):
    """Get file metadata by ID."""
    from ..models.file import File as FileModel

    service = get_file_service()
    file_response = await service.get_file(file_id)

    if not file_response:
        raise HTTPException(status_code=404, detail="File not found")

    # Check namespace permission (File model has namespace, FileResponse doesn't)
    file_doc = await FileModel.find_one({"file_id": file_id})
    if file_doc:
        identity = get_current_identity()
        await check_namespace_permission(identity, file_doc.namespace, "read")

    return file_response


@router.get(
    "/{file_id}/download",
    response_model=FileDownloadResponse,
    summary="Get download URL",
    description="""
Get a pre-signed URL for direct file download.

The URL is valid for the specified duration (default: 1 hour).
Use this for browser downloads or when sharing files externally.
    """,
    dependencies=[Depends(require_file_storage)]
)
async def get_download_url(
    file_id: str,
    expires_in: int = Query(3600, ge=60, le=86400, description="URL expiration in seconds (1 min to 24 hours)"),
    _: str = Depends(require_api_key)
):
    """Get a pre-signed download URL for a file."""
    service = get_file_service()

    # Check namespace permission before generating presigned URL
    from ..models.file import File as FileModel
    file_doc = await FileModel.find_one({"file_id": file_id})
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found")
    identity = get_current_identity()
    await check_namespace_permission(identity, file_doc.namespace, "read")

    try:
        response = await service.get_download_url(file_id, expires_in)
        if not response:
            raise HTTPException(status_code=404, detail="File not found")
        return response
    except FileServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    "/{file_id}/content",
    summary="Download file content",
    description="Download the file content directly (streaming response).",
    dependencies=[Depends(require_file_storage)]
)
async def download_file_content(
    file_id: str,
    _: str = Depends(require_api_key)
):
    """Download file content directly, streamed from storage."""
    from ..models.file import File as FileModel
    from ..models.file import FileStatus

    # Quick metadata lookup (no file content loaded yet)
    file_doc = await FileModel.find_one({"file_id": file_id})
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found")

    if file_doc.status == FileStatus.INACTIVE:
        raise HTTPException(status_code=400, detail="File has been deleted")

    # Check namespace permission
    identity = get_current_identity()
    await check_namespace_permission(identity, file_doc.namespace, "read")

    # Stream chunks directly from MinIO → browser (no full buffering)
    storage = get_file_storage_client()
    return StreamingResponse(
        storage.download_stream(file_doc.storage_key),
        media_type=file_doc.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file_doc.filename}"',
            "Content-Length": str(file_doc.size_bytes),
        }
    )


@router.patch(
    "",
    response_model=BulkResponse,
    summary="Update file metadata",
    description="Update metadata for one or more files (description, tags, category, etc.).",
    dependencies=[Depends(require_file_storage)]
)
async def update_files_metadata(
    items: list[UpdateFileItem] = Body(...),
    _: str = Depends(require_api_key)
):
    """Update metadata for one or more files."""
    from ..models.file import File as FileModel
    identity = get_current_identity()
    service = get_file_service()
    results = []
    for i, item in enumerate(items):
        try:
            file_doc = await FileModel.find_one({"file_id": item.file_id})
            if not file_doc:
                results.append(BulkResultItem(index=i, status="error", id=item.file_id, error="File not found"))
                continue
            await check_namespace_permission(identity, file_doc.namespace, "write")
            response = await service.update_metadata(item.file_id, item)
            if not response:
                results.append(BulkResultItem(index=i, status="error", id=item.file_id, error="File not found"))
            else:
                results.append(BulkResultItem(index=i, status="updated", id=item.file_id))
        except FileServiceError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.file_id, error=str(e)))
    await asyncio.sleep(get_throttle_delay())
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.delete(
    "",
    response_model=BulkResponse,
    summary="Soft-delete files",
    description="Soft-delete one or more files by setting their status to inactive. "
                "Set force=true per item to delete even if referenced by documents.",
    dependencies=[Depends(require_file_storage)]
)
async def delete_files(
    items: list[DeleteItem] = Body(...),
    _: str = Depends(require_api_key)
):
    """Soft-delete one or more files."""
    from ..models.file import File as FileModel
    identity = get_current_identity()
    service = get_file_service()
    results = []
    for i, item in enumerate(items):
        try:
            file_doc = await FileModel.find_one({"file_id": item.id})
            if not file_doc:
                results.append(BulkResultItem(index=i, status="error", id=item.id, error="File not found"))
                continue
            await check_namespace_permission(identity, file_doc.namespace, "write")
            success = await service.delete_file(item.id, force=item.force)
            if not success:
                results.append(BulkResultItem(index=i, status="error", id=item.id, error="File not found"))
            else:
                results.append(BulkResultItem(index=i, status="deleted", id=item.id))
        except FileServiceError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.id, error=str(e)))
    await asyncio.sleep(get_throttle_delay())
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.get(
    "/{file_id}/documents",
    response_model=FileDocumentsResponse,
    summary="List documents referencing this file",
    description="""
Find all active documents that reference a specific file.

Uses the file_references index for efficient lookup.
    """,
    dependencies=[Depends(require_file_storage)]
)
async def get_file_documents(
    file_id: str,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=100, description="Items per page"),
    _: str = Depends(require_api_key)
):
    """List documents that reference this file."""
    from ..models.document import Document as DocumentModel
    from ..models.file import File as FileModel

    # Verify file exists and check namespace permission
    file_doc = await FileModel.find_one({"file_id": file_id})
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found")

    identity = get_current_identity()
    await check_namespace_permission(identity, file_doc.namespace, "read")

    # Query documents with this file_id in file_references
    query = {
        "file_references.file_id": file_id,
        "status": "active",
    }

    total = await DocumentModel.find(query).count()
    skip = (page - 1) * page_size
    docs = await DocumentModel.find(query).skip(skip).limit(page_size).sort(
        [("created_at", -1)]
    ).to_list()

    items = []
    for doc in docs:
        # Find the field_path(s) referencing this file
        field_paths = []
        for ref in (doc.file_references or []):
            if ref.get("file_id") == file_id:
                field_paths.append(ref.get("field_path", "unknown"))

        items.append(FileDocumentRef(
            document_id=doc.document_id,
            template_id=doc.template_id,
            template_value=None,  # Could be populated from document.template_value
            field_path=", ".join(field_paths) if field_paths else "unknown",
            status=doc.status.value if hasattr(doc.status, 'value') else doc.status,
            created_at=doc.created_at.isoformat() if doc.created_at else None,
        ))

    return FileDocumentsResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 1,
    )


@router.delete(
    "/{file_id}/hard",
    summary="Hard-delete file",
    description="""
Permanently delete a file from storage and database.

Only works on files with status 'inactive'. Use soft-delete first.
    """,
    dependencies=[Depends(require_file_storage)]
)
async def hard_delete_file(
    file_id: str,
    _: str = Depends(require_api_key)
):
    """Permanently delete a file."""
    service = get_file_service()

    # Check namespace permission — hard delete requires admin
    from ..models.file import File as FileModel
    file_doc = await FileModel.find_one({"file_id": file_id})
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found")
    identity = get_current_identity()
    await check_namespace_permission(identity, file_doc.namespace, "admin")

    try:
        success = await service.hard_delete_file(file_id)
        if not success:
            raise HTTPException(status_code=404, detail="File not found")
        return {"status": "permanently_deleted", "file_id": file_id}
    except FileServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    "/orphans/list",
    response_model=list[FileResponse],
    summary="List orphan files",
    description="""
List files that are not referenced by any active document.

Useful for cleanup operations. By default, only returns orphans older than 24 hours
to allow time for documents to be created after file upload.
    """,
    dependencies=[Depends(require_file_storage)]
)
async def list_orphan_files(
    older_than_hours: int = Query(0, ge=0, le=720, description="Only return orphans older than N hours (0 = all)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number to return"),
    _: str = Depends(require_api_key)
):
    """List orphan files."""
    service = get_file_service()
    return await service.find_orphans(older_than_hours=older_than_hours, limit=limit)


@router.get(
    "/by-checksum/{checksum}",
    response_model=list[FileResponse],
    summary="Find files by checksum",
    description="Find files with the same content (duplicate detection).",
    dependencies=[Depends(require_file_storage)]
)
async def find_by_checksum(
    checksum: str,
    _: str = Depends(require_api_key)
):
    """Find files by checksum."""
    service = get_file_service()
    return await service.get_by_checksum(checksum)


@router.get(
    "/health/integrity",
    response_model=FileIntegrityResponse,
    summary="Check file integrity",
    description="""
Check file storage integrity.

Detects:
- Orphan files (uploaded but not referenced)
- Missing storage (file record exists but content missing from MinIO)
- Broken references (deleted files still referenced by documents)
    """,
    dependencies=[Depends(require_file_storage)]
)
async def check_file_integrity(
    _: str = Depends(require_api_key)
):
    """Check file integrity."""
    service = get_file_service()
    return await service.check_integrity()
