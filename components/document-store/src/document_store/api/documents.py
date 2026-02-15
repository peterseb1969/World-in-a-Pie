"""Document API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..models.document import DocumentStatus
from ..models.api_models import (
    DocumentCreateRequest,
    DocumentResponse,
    DocumentCreateResponse,
    DocumentListResponse,
    DocumentVersionResponse,
    DocumentQueryRequest,
    DocumentQueryResponse,
    BulkCreateRequest,
    BulkCreateResponse,
)
from ..services.document_service import get_document_service
from .auth import require_api_key

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.post(
    "",
    response_model=DocumentCreateResponse,
    summary="Create or update document",
    description="""
Create a new document or update an existing one based on identity hash.

The document is validated against the specified template before creation.
If a document with the same identity hash already exists, a new version
is created and the previous version is marked as inactive.
    """
)
async def create_document(
    request: DocumentCreateRequest,
    _: str = Depends(require_api_key)
):
    """Create or update a document. Namespace is specified in the request body (default: "wip")."""
    service = get_document_service()
    response, error = await service.create_document(request, namespace=request.namespace)

    if error:
        raise HTTPException(status_code=400, detail=error)

    return response


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
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
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
    "/{document_id}",
    summary="Soft-delete document",
    description="Soft-delete a document by setting its status to inactive."
)
async def delete_document(
    document_id: str,
    deleted_by: Optional[str] = Query(None, description="User performing the deletion"),
    _: str = Depends(require_api_key)
):
    """Soft-delete a document."""
    service = get_document_service()
    success = await service.delete_document(document_id, deleted_by)

    if not success:
        raise HTTPException(status_code=404, detail="Document not found")

    return {"status": "deleted", "document_id": document_id}


@router.post(
    "/{document_id}/archive",
    summary="Archive document",
    description="Archive a document by setting its status to archived."
)
async def archive_document(
    document_id: str,
    archived_by: Optional[str] = Query(None, description="User performing the archive"),
    _: str = Depends(require_api_key)
):
    """Archive a document."""
    service = get_document_service()
    success = await service.archive_document(document_id, archived_by)

    if not success:
        raise HTTPException(status_code=404, detail="Document not found")

    return {"status": "archived", "document_id": document_id}


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


@router.post(
    "/bulk",
    response_model=BulkCreateResponse,
    summary="Bulk create documents",
    description="""
Create multiple documents in a single request.

Each document is validated and created/updated independently.
By default, processing continues even if some items fail.
    """
)
async def bulk_create_documents(
    request: BulkCreateRequest,
    _: str = Depends(require_api_key)
):
    """Bulk create documents. Namespace is read from each item's namespace field (default: "wip")."""
    service = get_document_service()
    # Determine namespace from first item (all items in a bulk request share namespace)
    namespace = request.items[0].namespace if request.items else "wip"
    return await service.bulk_create(request, namespace=namespace)


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
