"""API endpoints for ontology relationship management and traversal."""

import math

from fastapi import APIRouter, Body, Depends, Query

from wip_auth import check_namespace_permission, get_current_identity, resolve_bulk_ids, resolve_or_404

from ..models.api_models import (
    BulkResponse,
    CreateRelationshipRequest,
    DeleteRelationshipRequest,
    RelationshipListResponse,
    RelationshipResponse,
    TraversalResponse,
)
from ..services.ontology_service import OntologyService
from .auth import require_api_key

router = APIRouter(prefix="/ontology", tags=["Ontology"])


# =============================================================================
# RELATIONSHIP CRUD
# =============================================================================

@router.post(
    "/relationships",
    response_model=BulkResponse,
    summary="Create term relationships"
)
async def create_relationships(
    items: list[CreateRelationshipRequest] = Body(...),
    namespace: str = Query(..., description="Namespace"),
    api_key: str = Depends(require_api_key),
) -> BulkResponse:
    """
    Create one or more typed relationships between terms.

    Relationship types include: is_a, part_of, has_part, maps_to, related_to,
    finding_site, causative_agent, or any custom type.
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "write")

    # Resolve term synonyms in bulk
    await resolve_bulk_ids(items, "source_term_id", "term", namespace)
    await resolve_bulk_ids(items, "target_term_id", "term", namespace)

    results = await OntologyService.create_relationships(namespace, items)
    succeeded = sum(1 for r in results if r.status == "created")
    return BulkResponse(
        results=results,
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )


@router.get(
    "/relationships",
    response_model=RelationshipListResponse,
    summary="List relationships for a term"
)
async def list_relationships(
    term_id: str = Query(..., description="Term ID to query relationships for"),
    direction: str = Query("outgoing", description="Direction: outgoing, incoming, or both"),
    relationship_type: str | None = Query(None, description="Filter by relationship type"),
    namespace: str = Query(..., description="Namespace"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Page size"),
    api_key: str = Depends(require_api_key),
) -> RelationshipListResponse:
    """List relationships for a term, with optional direction and type filtering."""
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "read")

    # Resolve term_id synonym
    term_id = await resolve_or_404(term_id, "term", namespace, param_name="term_id")

    items, total = await OntologyService.list_relationships(
        term_id=term_id,
        namespace=namespace,
        direction=direction,
        relationship_type=relationship_type,
        page=page,
        page_size=page_size,
    )
    return RelationshipListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
    )


@router.delete(
    "/relationships",
    response_model=BulkResponse,
    summary="Delete term relationships"
)
async def delete_relationships(
    items: list[DeleteRelationshipRequest] = Body(...),
    namespace: str = Query(..., description="Namespace"),
    api_key: str = Depends(require_api_key),
) -> BulkResponse:
    """
    Soft-delete one or more relationships (set status to inactive).
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "write")

    # Resolve term IDs in delete items
    await resolve_bulk_ids(items, "source_term_id", "term", namespace)
    await resolve_bulk_ids(items, "target_term_id", "term", namespace)

    results = await OntologyService.delete_relationships(namespace, items)
    succeeded = sum(1 for r in results if r.status in ("deleted", "skipped"))
    return BulkResponse(
        results=results,
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )


@router.get(
    "/relationships/all",
    response_model=RelationshipListResponse,
    summary="List all relationships (for batch sync)"
)
async def list_all_relationships(
    namespace: str = Query(..., description="Namespace"),
    relationship_type: str | None = Query(None, description="Filter by type"),
    source_terminology_id: str | None = Query(None, description="Filter by source terminology ID"),
    status: str = Query("active", description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Page size"),
    api_key: str = Depends(require_api_key),
) -> RelationshipListResponse:
    """
    List all relationships in a namespace (paginated).

    Unlike the per-term list endpoint, this returns ALL relationships,
    useful for batch sync and export operations. Use source_terminology_id
    to filter to a specific terminology.
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "read")

    # Resolve source_terminology_id synonym if provided
    if source_terminology_id:
        source_terminology_id = await resolve_or_404(
            source_terminology_id, "terminology", namespace, param_name="source_terminology_id"
        )

    items, total = await OntologyService.list_all_relationships(
        namespace=namespace,
        relationship_type=relationship_type,
        source_terminology_id=source_terminology_id,
        status=status,
        page=page,
        page_size=page_size,
    )
    return RelationshipListResponse(
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
    relationship_type: str = Query("is_a", description="Relationship type to traverse"),
    namespace: str = Query(..., description="Namespace"),
    max_depth: int = Query(10, ge=1, le=50, description="Maximum traversal depth"),
    api_key: str = Depends(require_api_key),
) -> TraversalResponse:
    """
    Traverse upward from a term, following outgoing relationships of the given type.

    For is_a relationships, also follows parent_term_id links for backward
    compatibility with simple hierarchical terminologies.
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "read")

    term_id = await resolve_or_404(term_id, "term", namespace, param_name="term_id")

    return await OntologyService.get_ancestors(
        term_id=term_id,
        namespace=namespace,
        relationship_type=relationship_type,
        max_depth=max_depth,
    )


@router.get(
    "/terms/{term_id}/descendants",
    response_model=TraversalResponse,
    summary="Get descendants of a term"
)
async def get_descendants(
    term_id: str,
    relationship_type: str = Query("is_a", description="Relationship type to traverse"),
    namespace: str = Query(..., description="Namespace"),
    max_depth: int = Query(10, ge=1, le=50, description="Maximum traversal depth"),
    api_key: str = Depends(require_api_key),
) -> TraversalResponse:
    """
    Traverse downward from a term, following incoming relationships of the given type.

    For is_a relationships, also includes children via parent_term_id.
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "read")

    term_id = await resolve_or_404(term_id, "term", namespace, param_name="term_id")

    return await OntologyService.get_descendants(
        term_id=term_id,
        namespace=namespace,
        relationship_type=relationship_type,
        max_depth=max_depth,
    )


@router.get(
    "/terms/{term_id}/parents",
    response_model=list[RelationshipResponse],
    summary="Get direct parents of a term"
)
async def get_parents(
    term_id: str,
    namespace: str = Query(..., description="Namespace"),
    api_key: str = Depends(require_api_key),
) -> list[RelationshipResponse]:
    """
    Get immediate parents of a term (non-transitive).

    Combines is_a relationships and parent_term_id.
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "read")

    term_id = await resolve_or_404(term_id, "term", namespace, param_name="term_id")

    return await OntologyService.get_parents(term_id, namespace)


@router.get(
    "/terms/{term_id}/children",
    response_model=list[RelationshipResponse],
    summary="Get direct children of a term"
)
async def get_children(
    term_id: str,
    namespace: str = Query(..., description="Namespace"),
    api_key: str = Depends(require_api_key),
) -> list[RelationshipResponse]:
    """
    Get immediate children of a term (non-transitive).

    Combines incoming is_a relationships and children via parent_term_id.
    """
    identity = get_current_identity()
    await check_namespace_permission(identity, namespace, "read")

    term_id = await resolve_or_404(term_id, "term", namespace, param_name="term_id")

    return await OntologyService.get_children(term_id, namespace)
