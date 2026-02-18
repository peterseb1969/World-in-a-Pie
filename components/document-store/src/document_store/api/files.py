"""File API endpoints for binary file storage."""

import asyncio
import math
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..models.file import FileStatus
from ..models.api_models import (
    BulkResultItem,
    BulkResponse,
    FileResponse,
    FileListResponse,
    FileDownloadResponse,
    FileUploadMetadata,
    UpdateFileItem,
    DeleteItem,
    FileIntegrityResponse,
)
from ..services.file_service import get_file_service, FileServiceError
from ..services.nats_client import get_throttle_delay
from ..services.file_storage_client import (
    get_file_storage_client,
    is_file_storage_enabled,
    FileStorageError,
)
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
    namespace: str = Form(default="wip", description="Namespace for the file"),
    file_id: Optional[str] = Form(None, description="Pre-assigned file ID (for restore/migration)"),
    description: Optional[str] = Form(None, description="File description"),
    tags: Optional[str] = Form(None, description="Comma-separated tags"),
    category: Optional[str] = Form(None, description="Classification category"),
    allowed_templates: Optional[str] = Form(None, description="Comma-separated template values"),
    _: str = Depends(require_api_key)
):
    """Upload a file to storage."""
    service = get_file_service()

    # Read file content
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

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
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "",
    response_model=FileListResponse,
    summary="List files",
    description="List files with optional filtering and pagination.",
    dependencies=[Depends(require_file_storage)]
)
async def list_files(
    namespace: str = Query(default="wip", description="Namespace to query"),
    status: Optional[FileStatus] = Query(None, description="Filter by status"),
    content_type: Optional[str] = Query(None, description="Filter by MIME type (e.g., 'image/*')"),
    category: Optional[str] = Query(None, description="Filter by category"),
    tags: Optional[str] = Query(None, description="Comma-separated tags (all must match)"),
    uploaded_by: Optional[str] = Query(None, description="Filter by uploader"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    _: str = Depends(require_api_key)
):
    """List files with pagination."""
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
    service = get_file_service()
    file_response = await service.get_file(file_id)

    if not file_response:
        raise HTTPException(status_code=404, detail="File not found")

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

    try:
        response = await service.get_download_url(file_id, expires_in)
        if not response:
            raise HTTPException(status_code=404, detail="File not found")
        return response
    except FileServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
    from ..models.file import File as FileModel, FileStatus

    # Quick metadata lookup (no file content loaded yet)
    file_doc = await FileModel.find_one({"file_id": file_id})
    if not file_doc:
        raise HTTPException(status_code=404, detail="File not found")

    if file_doc.status == FileStatus.INACTIVE:
        raise HTTPException(status_code=400, detail="File has been deleted")

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
    service = get_file_service()
    results = []
    for i, item in enumerate(items):
        try:
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
    service = get_file_service()
    results = []
    for i, item in enumerate(items):
        try:
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

    # Verify file exists
    service = get_file_service()
    file_response = await service.get_file(file_id)
    if not file_response:
        raise HTTPException(status_code=404, detail="File not found")

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

    try:
        success = await service.hard_delete_file(file_id)
        if not success:
            raise HTTPException(status_code=404, detail="File not found")
        return {"status": "permanently_deleted", "file_id": file_id}
    except FileServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
