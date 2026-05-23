"""Document API endpoints."""

import asyncio

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from wip_auth import (
    UserIdentity,
    check_namespace_permission,
    resolve_bulk_ids,
    resolve_namespace_filter,
    resolve_or_404,
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
    PatchDocumentItem,
    RelationshipListResponse,
    TraverseResponse,
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
    identity: UserIdentity = Depends(require_api_key)
):
    """Create or update documents. Namespace is read from each item (default: "wip").

    Template IDs accept both canonical UUIDs and human-readable values
    (e.g., "PATIENT" instead of "019..."). Values are resolved via Registry synonyms.
    """
    namespaces = {item.namespace for item in items}
    for ns in namespaces:
        await check_namespace_permission(identity, ns, "write")

    # Resolve template_id synonyms to canonical IDs (e.g., "PATIENT" → UUID).
    # namespace=None is correct here — each item carries its own namespace field,
    # and resolve_bulk_ids reads it per-item.
    await resolve_bulk_ids(items, "template_id", "template", namespace=None)

    service = get_document_service()

    if len(items) == 1:
        # Single item — use direct create path
        response, error = await service.create_document(items[0], namespace=items[0].namespace)
        if error:
            results = [BulkResultItem(index=0, status="error", error=error)]
        else:
            assert response is not None  # paired with error: when error is None, response is set
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


@router.patch(
    "",
    response_model=BulkResponse,
    summary="Patch documents (partial update)",
    description="""
Apply JSON Merge Patches (RFC 7396) to one or more existing documents.

Each item is processed independently and a new document version is created
on success. Errors are returned per item in the BulkResponse — the endpoint
always returns HTTP 200, matching the bulk-first contract.

Merge semantics:
- Objects at the same path are deep-merged
- Arrays are replaced (send the full array)
- A null value deletes the field

Constraints:
- Identity fields cannot be changed (use POST to create a new document)
- Namespace cannot be changed (PATCH only modifies `data`)
- Archived documents are rejected; unarchive first
- Optional per-item `if_match` provides optimistic concurrency control

After merge the full document is re-validated against the same template
version recorded on the existing document, term and file references are
re-resolved, and a new version is written. Reporting-sync sees the same
DOCUMENT_UPDATED event as POST-driven version bumps.
"""
)
async def patch_documents(
    items: list[PatchDocumentItem] = Body(...),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key),
):
    """Apply JSON Merge Patches to documents (bulk-first)."""
    # Resolve document_id synonyms in place. Resolution failures are silently
    # left as-is so the per-item flow returns 'not_found' rather than aborting
    # the whole batch.
    await resolve_bulk_ids(items, "document_id", "document", namespace=namespace)

    # CASE-384 — enforce write permission on each document's actual
    # namespace before mutating. Batched lookup: one find query over all
    # document_ids in the bulk, then per-id permission check (cache
    # amortises repeated namespaces). Aborts on first failure to match
    # the bulk-create convention.
    from ..models.document import Document as _Doc
    doc_ids = [item.document_id for item in items if item.document_id]
    if doc_ids:
        existing_docs = await _Doc.find({"document_id": {"$in": doc_ids}}).to_list()
        id_to_namespace = {d.document_id: d.namespace for d in existing_docs}
        for item in items:
            ns = id_to_namespace.get(item.document_id)
            if ns:
                await check_namespace_permission(identity, ns, "write")

    service = get_document_service()
    result = await service.bulk_patch(items)
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
    sort_by: str | None = Query(None, description="Sort field: created_at (default), updated_at, version, or data.<path>. Incompatible with cursor."),
    sort_order: str | None = Query(None, description="Sort order: asc | desc (default desc)"),
    identity: UserIdentity = Depends(require_api_key)
):
    """List documents with pagination.

    Use latest_only=true to return only the highest version of each document_id.
    Use cursor for efficient deep pagination (avoids skip/limit degradation).
    When cursor is provided, page parameter is ignored and total is -1, and
    sort_by/sort_order must be omitted (cursor mode is _id-ordered).
    """
    ns_filter = await resolve_namespace_filter(identity, namespace)

    # Resolve template_id synonym if provided (e.g., "PATIENT" → UUID)
    if template_id:
        template_id = await resolve_or_404(
            template_id, "template", namespace, param_name="template_id"
        )

    service = get_document_service()
    try:
        return await service.list_documents(
            template_id=template_id,
            template_value=template_value,
            status=status,
            page=page,
            page_size=page_size,
            latest_only=latest_only,
            cursor=cursor,
            ns_filter=ns_filter.query,
            sort_by=sort_by,
            sort_order=sort_order,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get document by ID",
    description="Retrieve a document by its stable ID. Returns latest version by default."
)
async def get_document(
    document_id: str,
    version: int | None = Query(None, description="Specific version (default: latest)"),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Get a document by stable ID. Returns latest version by default."""
    document_id = await resolve_or_404(document_id, "document", namespace=namespace, param_name="document_id")

    service = get_document_service()
    document = await service.get_document(document_id, version=version)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

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
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Get all versions of a document."""
    document_id = await resolve_or_404(document_id, "document", namespace=namespace, param_name="document_id")

    service = get_document_service()
    versions = await service.get_document_versions(document_id)

    if not versions:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check namespace — fetch the document to get its namespace
    doc = await service.get_document(document_id)
    if doc:
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
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Get a specific version of a document."""
    document_id = await resolve_or_404(document_id, "document", namespace=namespace, param_name="document_id")

    service = get_document_service()
    document = await service.get_document_version(document_id, version)

    if not document:
        raise HTTPException(status_code=404, detail="Document version not found")

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
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Get the latest version of a document."""
    document_id = await resolve_or_404(document_id, "document", namespace=namespace, param_name="document_id")

    service = get_document_service()
    document = await service.get_latest_document(document_id)

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    await check_namespace_permission(identity, document.namespace, "read")

    return document


@router.get(
    "/{document_id}/relationships",
    response_model=RelationshipListResponse,
    summary="List relationship documents touching this document",
    description="""
Return relationship documents (templates with usage='relationship')
that point at (incoming) or from (outgoing) the given document.

Backed by Mongo indexes on (template_id, data.source_ref) and
(template_id, data.target_ref) created lazily on first relationship-
document write.

Pass `?include=peers` (CASE-303) to embed a compact peer projection on
each item — the entity at the OTHER end of the edge — avoiding an N+1
fetch for relationship-sidebar rendering. Default response shape is
unchanged when `include` is absent or does not contain `peers`.
""",
)
async def get_document_relationships(
    document_id: str,
    direction: str = Query("both", description="incoming | outgoing | both"),
    template: str | None = Query(
        None, description="Comma-separated relationship template values to include (default: all)"),
    namespace: str | None = Query(None, description="Namespace; default = the document's namespace"),
    active_only: bool = Query(True, description="Exclude inactive/archived relationship docs"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    include: str | None = Query(
        None,
        description="Comma-separated optional inclusions. Currently supports: 'peers' (embed a compact peer projection on each item, CASE-303).",
    ),
    identity: UserIdentity = Depends(require_api_key),
):
    """List relationships incident to a document."""
    document_id = await resolve_or_404(document_id, "document", namespace=namespace, param_name="document_id")

    service = get_document_service()
    seed = await service.get_document(document_id)
    if not seed:
        raise HTTPException(status_code=404, detail="Document not found")

    await check_namespace_permission(identity, seed.namespace, "read")

    template_filter = [t.strip() for t in template.split(",")] if template else None
    include_set = (
        {tok.strip() for tok in include.split(",") if tok.strip()} if include else set()
    )
    try:
        return await service.find_relationships(
            document_id=document_id,
            direction=direction,
            template_filter=template_filter,
            namespace=namespace or seed.namespace,
            active_only=active_only,
            page=page,
            page_size=page_size,
            include_peers="peers" in include_set,
            identity=identity,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get(
    "/{document_id}/traverse",
    response_model=TraverseResponse,
    summary="N-hop relationship traversal from a document",
    description="""
BFS expansion through relationship documents from a seed document.
At each hop, finds relationship documents touching the current
frontier and adds the *other* endpoint document_ids to the next
frontier. Visited docs are skipped (cycles terminate).

Capped at depth=10 and max_nodes=1000 (safety bounds). When a cap
fires, the response sets truncated=true.
""",
)
async def traverse_document_relationships(
    document_id: str,
    depth: int = Query(1, ge=1, le=10, description="Number of relationship hops"),
    types: str | None = Query(
        None, description="Comma-separated relationship template values to traverse (default: all)"),
    direction: str = Query("outgoing", description="outgoing | incoming | both"),
    namespace: str | None = Query(None, description="Namespace; default = the seed document's namespace"),
    identity: UserIdentity = Depends(require_api_key),
):
    """Traverse relationship graph from a document."""
    document_id = await resolve_or_404(document_id, "document", namespace=namespace, param_name="document_id")

    service = get_document_service()
    seed = await service.get_document(document_id)
    if not seed:
        raise HTTPException(status_code=404, detail="Document not found")

    await check_namespace_permission(identity, seed.namespace, "read")

    types_filter = [t.strip() for t in types.split(",")] if types else None
    try:
        return await service.traverse_relationships(
            document_id=document_id,
            depth=depth,
            types_filter=types_filter,
            direction=direction,
            namespace=namespace or seed.namespace,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete(
    "",
    response_model=BulkResponse,
    summary="Delete documents",
    description="Delete one or more documents. Soft-delete by default. Set hard_delete=true to permanently remove (requires namespace deletion_mode='full')."
)
async def delete_documents(
    items: list[DeleteItem] = Body(...),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Delete one or more documents."""
    await resolve_bulk_ids(items, "id", "document", namespace=namespace)

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
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Archive one or more documents."""
    await resolve_bulk_ids(items, "id", "document", namespace=namespace)

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
    namespace: str | None = Query(None, description="Namespace for synonym resolution and filtering"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Query documents with filters."""
    if request.template_id:
        request.template_id = await resolve_or_404(
            request.template_id, "template", namespace=namespace, param_name="template_id"
        )

    ns_filter = await resolve_namespace_filter(identity, namespace=namespace)

    service = get_document_service()
    try:
        return await service.query_documents(request, ns_filter=ns_filter.query)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get(
    "/by-identity/{identity_hash}",
    response_model=DocumentResponse,
    summary="Get document by identity hash",
    description="Get the active document with the specified identity hash."
)
async def get_document_by_identity(
    identity_hash: str,
    namespace: str | None = Query(None, description="Filter by namespace (recommended to avoid cross-template ambiguity)"),
    template_id: str | None = Query(None, description="Filter by template_id (recommended to avoid cross-template ambiguity)"),
    include_inactive: bool = Query(False, description="Include inactive documents"),
    identity: UserIdentity = Depends(require_api_key)
):
    """Get a document by identity hash."""
    service = get_document_service()
    document = await service.get_document_by_identity(
        identity_hash, include_inactive,
        namespace=namespace, template_id=template_id,
    )

    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    await check_namespace_permission(identity, document.namespace, "read")

    return document
