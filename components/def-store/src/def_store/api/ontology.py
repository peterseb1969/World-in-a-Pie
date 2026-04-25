"""API endpoints for ontology term-relation management and traversal."""

import math

from fastapi import APIRouter, Body, Depends, Query

from wip_auth import (
    check_namespace_permission,
    get_current_identity,
    resolve_bulk_ids,
    resolve_namespace_filter,
    resolve_or_404,
)

from ..models.api_models import (
    BulkResponse,
    CreateTermRelationRequest,
    DeleteTermRelationRequest,
    TermRelationListResponse,
    TermRelationResponse,
    TraversalResponse,
)
from ..services.ontology_service import OntologyService
from .auth import require_api_key

router = APIRouter(prefix="/ontology", tags=["Ontology"])


# =============================================================================
# RELATIONSHIP CRUD
# =============================================================================

@router.post(
    "/term-relations",
    response_model=BulkResponse,
    summary="Create term relations"
)
async def create_term_relations(
    items: list[CreateTermRelationRequest] = Body(...),
    namespace: str = Query(..., description="Namespace"),
    api_key: str = Depends(require_api_key),
) -> BulkResponse:
    """
    Create one or more typed relations between terms.

    Relation types include: is_a, part_of, has_part, maps_to, related_to,
    finding_site, causative_agent, or any custom type.
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "write")

    # Resolve term synonyms in bulk
    await resolve_bulk_ids(items, "source_term_id", "term", namespace)
    await resolve_bulk_ids(items, "target_term_id", "term", namespace)

    results = await OntologyService.create_term_relations(namespace, items)
    succeeded = sum(1 for r in results if r.status == "created")
    return BulkResponse(
        results=results,
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )


@router.get(
    "/term-relations",
    response_model=TermRelationListResponse,
    summary="List relations for a term"
)
async def list_term_relations(
    term_id: str = Query(..., description="Term ID to query relations for"),
    direction: str = Query("outgoing", description="Direction: outgoing, incoming, or both"),
    relation_type: str | None = Query(None, description="Filter by relation type"),
    namespace: str | None = Query(default=None, description="Namespace (omit for all accessible)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Page size (max 1000)"),
    api_key: str = Depends(require_api_key),
) -> TermRelationListResponse:
    """List relations for a term, with optional direction and type filtering."""
    identity = get_current_identity()
    ns_filter = await resolve_namespace_filter(identity, namespace)

    # Resolve term_id synonym
    term_id = await resolve_or_404(term_id, "term", namespace, param_name="term_id")

    items, total = await OntologyService.list_term_relations(
        term_id=term_id,
        ns_filter=ns_filter.query,
        direction=direction,
        relation_type=relation_type,
        page=page,
        page_size=page_size,
    )
    return TermRelationListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.delete(
    "/term-relations",
    response_model=BulkResponse,
    summary="Delete term relations"
)
async def delete_term_relations(
    items: list[DeleteTermRelationRequest] = Body(...),
    namespace: str = Query(..., description="Namespace"),
    api_key: str = Depends(require_api_key),
) -> BulkResponse:
    """
    Soft-delete one or more relations (set status to inactive).
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "write")

    # Resolve term IDs in delete items
    await resolve_bulk_ids(items, "source_term_id", "term", namespace)
    await resolve_bulk_ids(items, "target_term_id", "term", namespace)

    results = await OntologyService.delete_term_relations(namespace, items)
    succeeded = sum(1 for r in results if r.status in ("deleted", "skipped"))
    return BulkResponse(
        results=results,
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )


@router.get(
    "/term-relations/all",
    response_model=TermRelationListResponse,
    summary="List all relations (for batch sync)"
)
async def list_all_term_relations(
    namespace: str | None = Query(default=None, description="Namespace (omit for all accessible)"),
    relation_type: str | None = Query(None, description="Filter by type"),
    source_terminology_id: str | None = Query(None, description="Filter by source terminology ID"),
    status: str = Query("active", description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Page size (max 1000)"),
    api_key: str = Depends(require_api_key),
) -> TermRelationListResponse:
    """
    List all relations (paginated).

    Unlike the per-term list endpoint, this returns ALL relations,
    useful for batch sync and export operations. Use source_terminology_id
    to filter to a specific terminology. Omit namespace for cross-namespace results.
    """
    identity = get_current_identity()
    ns_filter = await resolve_namespace_filter(identity, namespace)

    # Resolve source_terminology_id synonym if provided
    if source_terminology_id:
        source_terminology_id = await resolve_or_404(
            source_terminology_id, "terminology", namespace, param_name="source_terminology_id"
        )

    items, total = await OntologyService.list_all_term_relations(
        ns_filter=ns_filter.query,
        relation_type=relation_type,
        source_terminology_id=source_terminology_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return TermRelationListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )


# =============================================================================
# TRAVERSAL QUERIES
# =============================================================================

@router.get(
    "/terms/{term_id}/ancestors",
    response_model=TraversalResponse,
    summary="Get ancestors of a term"
)
async def get_ancestors(
    term_id: str,
    relation_type: str = Query("is_a", description="Relation type to traverse"),
    namespace: str = Query(..., description="Namespace"),
    max_depth: int = Query(10, ge=1, le=50, description="Maximum traversal depth"),
    api_key: str = Depends(require_api_key),
) -> TraversalResponse:
    """
    Traverse upward from a term, following outgoing relations of the given type.

    For is_a relations, also follows parent_term_id links for backward
    compatibility with simple hierarchical terminologies.
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "read")

    term_id = await resolve_or_404(term_id, "term", namespace, param_name="term_id")

    return await OntologyService.get_ancestors(
        term_id=term_id,
        namespace=namespace,
        relation_type=relation_type,
        max_depth=max_depth,
    )


@router.get(
    "/terms/{term_id}/descendants",
    response_model=TraversalResponse,
    summary="Get descendants of a term"
)
async def get_descendants(
    term_id: str,
    relation_type: str = Query("is_a", description="Relation type to traverse"),
    namespace: str = Query(..., description="Namespace"),
    max_depth: int = Query(10, ge=1, le=50, description="Maximum traversal depth"),
    api_key: str = Depends(require_api_key),
) -> TraversalResponse:
    """
    Traverse downward from a term, following incoming relations of the given type.

    For is_a relations, also includes children via parent_term_id.
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "read")

    term_id = await resolve_or_404(term_id, "term", namespace, param_name="term_id")

    return await OntologyService.get_descendants(
        term_id=term_id,
        namespace=namespace,
        relation_type=relation_type,
        max_depth=max_depth,
    )


@router.get(
    "/terms/{term_id}/parents",
    response_model=list[TermRelationResponse],
    summary="Get direct parents of a term"
)
async def get_parents(
    term_id: str,
    namespace: str = Query(..., description="Namespace"),
    api_key: str = Depends(require_api_key),
) -> list[TermRelationResponse]:
    """
    Get immediate parents of a term (non-transitive).

    Combines is_a relations and parent_term_id.
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "read")

    term_id = await resolve_or_404(term_id, "term", namespace, param_name="term_id")

    return await OntologyService.get_parents(term_id, namespace)


@router.get(
    "/terms/{term_id}/children",
    response_model=list[TermRelationResponse],
    summary="Get direct children of a term"
)
async def get_children(
    term_id: str,
    namespace: str = Query(..., description="Namespace"),
    api_key: str = Depends(require_api_key),
) -> list[TermRelationResponse]:
    """
    Get immediate children of a term (non-transitive).

    Combines incoming is_a relations and children via parent_term_id.
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "read")

    term_id = await resolve_or_404(term_id, "term", namespace, param_name="term_id")

    return await OntologyService.get_children(term_id, namespace)
