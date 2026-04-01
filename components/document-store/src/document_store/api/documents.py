"""Document API endpoints."""

import asyncio
import contextlib

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from wip_auth import (
    EntityNotFoundError,
    check_namespace_permission,
    get_current_identity,
    resolve_accessible_namespaces,
    resolve_entity_id,
)

from ..models.api_models import (
    ArchiveItem,
    BulkResponse,
    BulkResultItem,
    DeleteItem,
    DocumentCreateRequest,
    DocumentListResponse,
    DocumentQueryRequest,
    DocumentQueryResponse,
    DocumentResponse,
    DocumentVersionResponse,
)
from ..models.document import DocumentStatus
from ..services.document_service import get_document_service
from ..services.nats_client import get_throttle_delay
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
    """Create or update documents. Namespace is read from each item (default: "wip").

    Template IDs accept both canonical UUIDs and human-readable values
    (e.g., "PATIENT" instead of "019..."). Values are resolved via Registry synonyms.
    """
    identity = get_current_identity()
    namespaces = {item.namespace for item in items}
    for ns in namespaces:
        await check_namespace_permission(identity, ns, "write")

    # Resolve template_id synonyms to canonical IDs (e.g., "PATIENT" → UUID)
    for item in items:
        with contextlib.suppress(EntityNotFoundError):
            item.template_id = await resolve_entity_id(
                item.template_id, "template", item.namespace
            )

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
        result = BulkResponse(results=results, total=1, succeeded=succeeded, failed=failed)
    else:
        # Bulk path — uses cache warmup and batch Registry calls
        namespace = items[0].namespace
        result = await service.bulk_create(items, namespace=namespace, continue_on_error=continue_on_error)

    await asyncio.sleep(get_throttle_delay())
    return result


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List documents",
    description="List documents with optional filtering and pagination."
)
async def list_documents(
    namespace: str | None = Query(default=None, description="Namespace to query (omit for all)"),
    template_id: str | None = Query(None, description="Filter by template ID"),
    template_value: str | None = Query(None, description="Filter by template value (e.g., PLANNED_VISIT)"),
    status: DocumentStatus | None = Query(None, description="Filter by status"),
    latest_only: bool = Query(False, description="Only return the latest version of each document"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Items per page (max 1000)"),
    cursor: str | None = Query(None, description="Cursor for cursor-based pagination (MongoDB _id of last item)"),
    _: str = Depends(require_api_key)
):
    """List documents with pagination.

    Use latest_only=true to return only the highest version of each document_id.
    Use cursor for efficient deep pagination (avoids skip/limit degradation).
    When cursor is provided, page parameter is ignored and total is -1.
    """
    identity = get_current_identity()
    allowed_namespaces = None
    if namespace:
        await check_namespace_permission(identity, namespace, "read")
    else:
        allowed_namespaces = await resolve_accessible_namespaces(identity)

    # Resolve template_id synonym if provided (e.g., "PATIENT" → UUID)
    if template_id:
        with contextlib.suppress(EntityNotFoundError):
            template_id = await resolve_entity_id(
                template_id, "template", namespace
            )

    service = get_document_service()
    return await service.list_documents(
        template_id=template_id,
        template_value=template_value,
        status=status,
        page=page,
        page_size=page_size,
        namespace=namespace,
        latest_only=latest_only,
        cursor=cursor,
        allowed_namespaces=allowed_namespaces,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get document by ID",
    description="Retrieve a document by its stable ID. Returns latest version by default."
)
async def get_document(
    document_id: str,
    version: int | None = Query(None, description="Specific version (default: latest)"),
    _: str = Depends(require_api_key)
):
    """Get a document by stable ID. Returns latest version by default."""
    service = get_document_service()
    document = await service.get_document(document_id, version=version)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    identity = get_current_identity()
    await check_namespace_permission(identity, document.namespace, "read")

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

    # Check namespace — fetch the document to get its namespace
    doc = await service.get_document(document_id)
    if doc:
        identity = get_current_identity()
        await check_namespace_permission(identity, doc.namespace, "read")

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

    identity = get_current_identity()
    await check_namespace_permission(identity, document.namespace, "read")

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

    identity = get_current_identity()
    await check_namespace_permission(identity, document.namespace, "read")

    return document


@router.delete(
    "",
    response_model=BulkResponse,
    summary="Delete documents",
    description="Delete one or more documents. Soft-delete by default. Set hard_delete=true to permanently remove (requires namespace deletion_mode='full')."
)
async def delete_documents(
    items: list[DeleteItem] = Body(...),
    _: str = Depends(require_api_key)
):
    """Delete one or more documents."""
    identity = get_current_identity()
    service = get_document_service()
    results = []
    for i, item in enumerate(items):
        try:
            doc = await service.get_document(item.id)
            if not doc:
                results.append(BulkResultItem(index=i, status="error", id=item.id, error="Document not found"))
                continue
            await check_namespace_permission(identity, doc.namespace, "write")
            success = await service.delete_document(
                item.id, item.updated_by,
                hard_delete=item.hard_delete,
                version=item.version,
            )
            if not success:
                results.append(BulkResultItem(index=i, status="error", id=item.id, error="Document not found"))
            else:
                results.append(BulkResultItem(index=i, status="deleted", id=item.id))
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.id, error=str(e)))
    await asyncio.sleep(get_throttle_delay())
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
    identity = get_current_identity()
    service = get_document_service()
    results = []
    for i, item in enumerate(items):
        doc = await service.get_document(item.id)
        if not doc:
            results.append(BulkResultItem(index=i, status="error", id=item.id, error="Document not found"))
            continue
        await check_namespace_permission(identity, doc.namespace, "write")
        success = await service.archive_document(item.id, item.archived_by)
        if not success:
            results.append(BulkResultItem(index=i, status="error", id=item.id, error="Document not found"))
        else:
            results.append(BulkResultItem(index=i, status="updated", id=item.id))
    await asyncio.sleep(get_throttle_delay())
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
    identity = get_current_identity()
    allowed_namespaces = await resolve_accessible_namespaces(identity)

    service = get_document_service()
    return await service.query_documents(request, allowed_namespaces=allowed_namespaces)


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

    identity = get_current_identity()
    await check_namespace_permission(identity, document.namespace, "read")

    return document
