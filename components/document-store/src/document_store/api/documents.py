"""Document API endpoints."""

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from ..models.document import DocumentStatus
from ..models.api_models import (
    BulkResultItem,
    BulkResponse,
    DocumentCreateRequest,
    DocumentResponse,
    DocumentListResponse,
    DocumentVersionResponse,
    DocumentQueryRequest,
    DocumentQueryResponse,
    DeleteItem,
    ArchiveItem,
)
from ..services.document_service import get_document_service
from .auth import require_api_key

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.post(
    "",
    response_model=BulkResponse,
    summary="Create or update documents",
    description="""
Create one or more documents, or update existing ones based on identity hash.

Each document is validated against the specified template before creation.
For single items, uses direct creation. For multiple items, uses optimized
batch operations with cache warmup.
    """
)
async def create_documents(
    items: list[DocumentCreateRequest] = Body(...),
    continue_on_error: bool = Query(True, description="Continue processing if an item fails"),
    _: str = Depends(require_api_key)
):
    """Create or update documents. Namespace is read from each item (default: "wip")."""
    service = get_document_service()

    if len(items) == 1:
        # Single item — use direct create path
        response, error = await service.create_document(items[0], namespace=items[0].namespace)
        if error:
            results = [BulkResultItem(index=0, status="error", error=error)]
        else:
            if response.is_new:
                status = "created"
            elif response.previous_version is not None:
                status = "updated"
            else:
                status = "skipped"
            results = [BulkResultItem(
                index=0, status=status,
                id=response.document_id, document_id=response.document_id,
                identity_hash=response.identity_hash, version=response.version,
                is_new=response.is_new, warnings=response.warnings,
            )]
        succeeded = sum(1 for r in results if r.status != "error")
        failed = sum(1 for r in results if r.status == "error")
        return BulkResponse(results=results, total=1, succeeded=succeeded, failed=failed)
    else:
        # Bulk path — uses cache warmup and batch Registry calls
        namespace = items[0].namespace if items else "wip"
        return await service.bulk_create(items, namespace=namespace, continue_on_error=continue_on_error)


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List documents",
    description="List documents with optional filtering and pagination."
)
async def list_documents(
    namespace: Optional[str] = Query(default=None, description="Namespace to query (omit for all)"),
    template_id: Optional[str] = Query(None, description="Filter by template ID"),
    template_value: Optional[str] = Query(None, description="Filter by template value (e.g., PLANNED_VISIT)"),
    status: Optional[DocumentStatus] = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    _: str = Depends(require_api_key)
):
    """List documents with pagination."""
    service = get_document_service()
    return await service.list_documents(
        template_id=template_id,
        template_value=template_value,
        status=status,
        page=page,
        page_size=page_size,
        namespace=namespace
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get document by ID",
    description="Retrieve a document by its stable ID. Returns latest version by default."
)
async def get_document(
    document_id: str,
    version: Optional[int] = Query(None, description="Specific version (default: latest)"),
    _: str = Depends(require_api_key)
):
    """Get a document by stable ID. Returns latest version by default."""
    service = get_document_service()
    document = await service.get_document(document_id, version=version)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    return document


@router.get(
    "/{document_id}/versions",
    response_model=DocumentVersionResponse,
    summary="Get document versions",
    description="Get all versions of a document by its ID."
)
async def get_document_versions(
    document_id: str,
    _: str = Depends(require_api_key)
):
    """Get all versions of a document."""
    service = get_document_service()
    versions = await service.get_document_versions(document_id)

    if not versions:
        raise HTTPException(status_code=404, detail="Document not found")

    return versions


@router.get(
    "/{document_id}/versions/{version}",
    response_model=DocumentResponse,
    summary="Get specific document version",
    description="Get a specific version of a document."
)
async def get_document_version(
    document_id: str,
    version: int,
    _: str = Depends(require_api_key)
):
    """Get a specific version of a document."""
    service = get_document_service()
    document = await service.get_document_version(document_id, version)

    if not document:
        raise HTTPException(status_code=404, detail="Document version not found")

    return document


@router.get(
    "/{document_id}/latest",
    response_model=DocumentResponse,
    summary="Get latest version of document",
    description="""
Get the latest version of a document given any document ID.

This is useful when you have a reference to an old version but want
the current data. The response includes the latest document ID and version.
    """
)
async def get_latest_document(
    document_id: str,
    _: str = Depends(require_api_key)
):
    """Get the latest version of a document."""
    service = get_document_service()
    document = await service.get_latest_document(document_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    return document


@router.delete(
    "",
    response_model=BulkResponse,
    summary="Soft-delete documents",
    description="Soft-delete one or more documents by setting their status to inactive."
)
async def delete_documents(
    items: list[DeleteItem] = Body(...),
    _: str = Depends(require_api_key)
):
    """Soft-delete one or more documents."""
    service = get_document_service()
    results = []
    for i, item in enumerate(items):
        success = await service.delete_document(item.id, item.updated_by)
        if not success:
            results.append(BulkResultItem(index=i, status="error", id=item.id, error="Document not found"))
        else:
            results.append(BulkResultItem(index=i, status="deleted", id=item.id))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.post(
    "/archive",
    response_model=BulkResponse,
    summary="Archive documents",
    description="Archive one or more documents by setting their status to archived."
)
async def archive_documents(
    items: list[ArchiveItem] = Body(...),
    _: str = Depends(require_api_key)
):
    """Archive one or more documents."""
    service = get_document_service()
    results = []
    for i, item in enumerate(items):
        success = await service.archive_document(item.id, item.archived_by)
        if not success:
            results.append(BulkResultItem(index=i, status="error", id=item.id, error="Document not found"))
        else:
            results.append(BulkResultItem(index=i, status="updated", id=item.id))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.post(
    "/query",
    response_model=DocumentQueryResponse,
    summary="Query documents",
    description="""
Query documents with complex filters.

Supports filtering on any field including nested data fields.
Example filters:
- {"field": "data.status", "operator": "eq", "value": "active"}
- {"field": "created_at", "operator": "gte", "value": "2024-01-01"}
- {"field": "data.tags", "operator": "in", "value": ["important", "urgent"]}
    """
)
async def query_documents(
    request: DocumentQueryRequest,
    _: str = Depends(require_api_key)
):
    """Query documents with filters."""
    service = get_document_service()
    return await service.query_documents(request)


@router.get(
    "/by-identity/{identity_hash}",
    response_model=DocumentResponse,
    summary="Get document by identity hash",
    description="Get the active document with the specified identity hash."
)
async def get_document_by_identity(
    identity_hash: str,
    include_inactive: bool = Query(False, description="Include inactive documents"),
    _: str = Depends(require_api_key)
):
    """Get a document by identity hash."""
    service = get_document_service()
    document = await service.get_document_by_identity(identity_hash, include_inactive)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    return document
