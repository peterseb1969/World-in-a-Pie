"""API endpoints for terminology management."""

import math

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from wip_auth import (
    UserIdentity,
    check_namespace_permission,
    resolve_bulk_ids,
    resolve_namespace_filter,
    resolve_or_404,
)

from ..models.api_models import (
    BulkResponse,
    BulkResultItem,
    CreateTerminologyRequest,
    DeleteItem,
    TerminologyListResponse,
    TerminologyResponse,
    UpdateTerminologyItem,
)
from ..services.dependency_service import DependencyService, TerminologyDependencies
from ..services.registry_client import RegistryError
from ..services.terminology_service import TerminologyService
from .auth import require_api_key

router = APIRouter(prefix="/terminologies", tags=["Terminologies"])


@router.post("", response_model=BulkResponse, summary="Create terminologies")
async def create_terminologies(
    items: list[CreateTerminologyRequest] = Body(...),
    identity: UserIdentity = Depends(require_api_key)
) -> BulkResponse:
    """
    Create one or more terminologies (controlled vocabularies).

    Each terminology will be registered with the Registry service to get
    a unique ID. Namespace is specified per item (default: "wip").
    """
    namespaces = {item.namespace for item in items}
    for ns in namespaces:
        await check_namespace_permission(identity, ns, "write")

    results = []
    for i, item in enumerate(items):
        try:
            result = await TerminologyService.create_terminology(item, namespace=item.namespace)
            results.append(BulkResultItem(index=i, status="created", id=result.terminology_id))
        except (ValueError, HTTPException) as e:
            results.append(BulkResultItem(index=i, status="error", error=str(e)))
        except RegistryError as e:
            results.append(BulkResultItem(index=i, status="error", error=f"Registry error: {e!s}"))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.get("", response_model=TerminologyListResponse, summary="List terminologies")
async def list_terminologies(
    namespace: str | None = Query(default=None, description="Namespace to query (omit for all)"),
    status: str | None = Query(None, description="Filter by status"),
    value: str | None = Query(None, description="Filter by exact value match"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=1000, description="Items per page (max 1000)"),
    identity: UserIdentity = Depends(require_api_key)
) -> TerminologyListResponse:
    """List all terminologies with pagination and optional filters."""
    ns_filter = await resolve_namespace_filter(identity, namespace)

    terminologies, total = await TerminologyService.list_terminologies(
        status=status,
        value=value,
        page=page,
        page_size=page_size,
        ns_filter=ns_filter.query,
    )
    return TerminologyListResponse(
        items=terminologies,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0
    )


@router.get("/by-value/{value}", response_model=TerminologyResponse, summary="Get a terminology by value")
async def get_terminology_by_value(
    value: str,
    namespace: str | None = Query(default=None, description="Namespace to search in (omit for all)"),
    identity: UserIdentity = Depends(require_api_key)
) -> TerminologyResponse:
    """Get a terminology by its value (e.g., DOC_STATUS)."""
    if namespace:
        await check_namespace_permission(identity, namespace, "read")

    result = await TerminologyService.get_terminology(value=value, namespace=namespace)
    if not result:
        raise HTTPException(status_code=404, detail="Terminology not found")
    return result


@router.get("/{terminology_id}", response_model=TerminologyResponse, summary="Get a terminology")
async def get_terminology(
    terminology_id: str,
    namespace: str | None = Query(default=None, description="Namespace for value fallback lookup"),
    identity: UserIdentity = Depends(require_api_key)
) -> TerminologyResponse:
    """
    Get a terminology by ID.

    The ID can be either the Registry ID or the value (DOC_STATUS).
    When falling back to value lookup, namespace is used for scoping.
    """
    # Resolve synonym (e.g., "STATUS" → UUID)
    terminology_id = await resolve_or_404(
        terminology_id, "terminology", namespace, param_name="terminology_id"
    )

    # Try as ID first, then as value
    result = await TerminologyService.get_terminology(terminology_id=terminology_id)
    if not result:
        result = await TerminologyService.get_terminology(value=terminology_id, namespace=namespace)

    if not result:
        raise HTTPException(status_code=404, detail="Terminology not found")

    # CASE-384 — enforce read permission on the entity's actual namespace.
    # Returns 404 ("Namespace not found") on permission failure, which
    # also prevents leaking which IDs exist in which namespaces.
    await check_namespace_permission(identity, result.namespace, "read")

    return result


@router.put("", response_model=BulkResponse, summary="Update terminologies")
async def update_terminologies(
    items: list[UpdateTerminologyItem] = Body(...),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key)
) -> BulkResponse:
    """
    Update one or more terminologies.

    If a value changes, a synonym will be added in the Registry to allow
    lookups by both old and new values.
    """
    await resolve_bulk_ids(items, "terminology_id", "terminology", namespace=namespace)

    # CASE-384 — enforce write permission on each item's actual namespace.
    # Batched lookup: one Mongo query over all terminology_ids, then
    # per-id permission check. Saves N-1 round-trips on cross-namespace
    # bulks vs. the original per-item find_one. Aborts on first failure
    # to match the bulk-create convention.
    from ..models.terminology import Terminology as _T
    ids = [item.terminology_id for item in items if item.terminology_id]
    if ids:
        existing_docs = await _T.find({"terminology_id": {"$in": ids}}).to_list()
        id_to_namespace = {d.terminology_id: d.namespace for d in existing_docs}
        for item in items:
            ns = id_to_namespace.get(item.terminology_id)
            if ns:
                await check_namespace_permission(identity, ns, "write")

    results = []
    for i, item in enumerate(items):
        try:
            result = await TerminologyService.update_terminology(item.terminology_id, item)
            if not result:
                results.append(BulkResultItem(index=i, status="error", id=item.terminology_id, error="Terminology not found"))
            else:
                results.append(BulkResultItem(index=i, status="updated", id=item.terminology_id))
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.terminology_id, error=str(e)))
        except RegistryError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.terminology_id, error=f"Registry error: {e!s}"))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.get(
    "/{terminology_id}/dependencies",
    response_model=TerminologyDependencies,
    summary="Get terminology dependencies"
)
async def get_terminology_dependencies(
    terminology_id: str,
    identity: UserIdentity = Depends(require_api_key)
) -> TerminologyDependencies:
    """
    Get dependencies of a terminology.

    Returns information about what depends on this terminology:
    - Templates that reference it via terminology_ref fields

    Use this to check before deactivating a terminology.
    """
    terminology_id = await resolve_or_404(
        terminology_id, "terminology", namespace=None, param_name="terminology_id"
    )

    # CASE-384 — even dependency metadata leaks information (it confirms
    # the terminology exists in the namespace and lists template names
    # referencing it). Gate by read permission on the entity's namespace.
    from ..models.terminology import Terminology as _T
    existing = await _T.find_one({"terminology_id": terminology_id})
    if existing:
        await check_namespace_permission(identity, existing.namespace, "read")

    try:
        return await DependencyService.check_terminology_dependencies(terminology_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.post("/{terminology_id}/restore", response_model=TerminologyResponse, summary="Restore a terminology")
async def restore_terminology(
    terminology_id: str,
    restore_terms: bool = Query(True, description="Also reactivate inactive terms"),
    identity: UserIdentity = Depends(require_api_key)
) -> TerminologyResponse:
    """
    Restore a soft-deleted (inactive) terminology back to active status.

    By default also reactivates all terms that were deactivated with it.
    Set restore_terms=false to restore only the terminology itself.
    """
    terminology_id = await resolve_or_404(
        terminology_id, "terminology", namespace=None, param_name="terminology_id"
    )

    # CASE-384 — restore is a mutation; require write on the terminology's
    # namespace. Lookup the entity first to resolve the namespace, then
    # check before restoring.
    from ..models.terminology import Terminology as _T
    existing = await _T.find_one({"terminology_id": terminology_id})
    if existing:
        await check_namespace_permission(identity, existing.namespace, "write")

    result = await TerminologyService.restore_terminology(
        terminology_id=terminology_id,
        restore_terms=restore_terms
    )
    if not result:
        raise HTTPException(status_code=404, detail="Terminology not found")
    return result


@router.delete("", response_model=BulkResponse, summary="Delete terminologies")
async def delete_terminologies(
    items: list[DeleteItem] = Body(...),
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    identity: UserIdentity = Depends(require_api_key)
) -> BulkResponse:
    """
    Soft-delete one or more terminologies (set status to inactive).

    All terms in each terminology will also be deactivated.
    Set force=true per item to delete even if templates reference it.
    """
    await resolve_bulk_ids(items, "id", "terminology", namespace=namespace)

    # CASE-384 — batched namespace lookup + permission check, same shape
    # as update_terminologies above.
    from ..models.terminology import Terminology as _T
    ids = [item.id for item in items if item.id]
    if ids:
        existing_docs = await _T.find({"terminology_id": {"$in": ids}}).to_list()
        id_to_namespace = {d.terminology_id: d.namespace for d in existing_docs}
        for item in items:
            ns = id_to_namespace.get(item.id)
            if ns:
                await check_namespace_permission(identity, ns, "write")

    results = []
    for i, item in enumerate(items):
        try:
            # Check dependencies unless forcing
            if not item.force:
                deps = await DependencyService.check_terminology_dependencies(item.id)
                if deps.template_count > 0:
                    results.append(BulkResultItem(
                        index=i, status="error", id=item.id,
                        error=f"Terminology has {deps.template_count} dependent templates. Use force=true to delete anyway."
                    ))
                    continue

            success = await TerminologyService.delete_terminology(
                terminology_id=item.id, updated_by=item.updated_by,
                hard_delete=item.hard_delete,
            )
            if not success:
                results.append(BulkResultItem(index=i, status="error", id=item.id, error="Terminology not found"))
            else:
                results.append(BulkResultItem(index=i, status="deleted", id=item.id))
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.id, error=str(e)))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )
