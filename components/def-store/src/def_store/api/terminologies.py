"""API endpoints for terminology management."""

import math
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query, Depends

from ..models.api_models import (
    BulkResultItem,
    BulkResponse,
    CreateTerminologyRequest,
    UpdateTerminologyItem,
    DeleteItem,
    TerminologyResponse,
    TerminologyListResponse,
)
from ..services.terminology_service import TerminologyService
from ..services.registry_client import RegistryError
from ..services.dependency_service import DependencyService, TerminologyDependencies
from wip_auth import check_namespace_permission, get_current_identity
from .auth import require_api_key

router = APIRouter(prefix="/terminologies", tags=["Terminologies"])


@router.post("", response_model=BulkResponse, summary="Create terminologies")
async def create_terminologies(
    items: list[CreateTerminologyRequest] = Body(...),
    api_key: str = Depends(require_api_key)
) -> BulkResponse:
    """
    Create one or more terminologies (controlled vocabularies).

    Each terminology will be registered with the Registry service to get
    a unique ID. Namespace is specified per item (default: "wip").
    """
    identity = get_current_identity()
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
            results.append(BulkResultItem(index=i, status="error", error=f"Registry error: {str(e)}"))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.get("", response_model=TerminologyListResponse, summary="List terminologies")
async def list_terminologies(
    namespace: Optional[str] = Query(default=None, description="Namespace to query (omit for all)"),
    status: Optional[str] = Query(None, description="Filter by status"),
    value: Optional[str] = Query(None, description="Filter by exact value match"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    api_key: str = Depends(require_api_key)
) -> TerminologyListResponse:
    """List all terminologies with pagination and optional filters."""
    if namespace:
        identity = get_current_identity()
        await check_namespace_permission(identity, namespace, "read")

    terminologies, total = await TerminologyService.list_terminologies(
        status=status,
        value=value,
        page=page,
        page_size=page_size,
        namespace=namespace
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
    namespace: Optional[str] = Query(default=None, description="Namespace to search in (omit for all)"),
    api_key: str = Depends(require_api_key)
) -> TerminologyResponse:
    """Get a terminology by its value (e.g., DOC_STATUS)."""
    if namespace:
        identity = get_current_identity()
        await check_namespace_permission(identity, namespace, "read")

    result = await TerminologyService.get_terminology(value=value, namespace=namespace)
    if not result:
        raise HTTPException(status_code=404, detail="Terminology not found")
    return result


@router.get("/{terminology_id}", response_model=TerminologyResponse, summary="Get a terminology")
async def get_terminology(
    terminology_id: str,
    namespace: Optional[str] = Query(default=None, description="Namespace for value fallback lookup"),
    api_key: str = Depends(require_api_key)
) -> TerminologyResponse:
    """
    Get a terminology by ID.

    The ID can be either the Registry ID or the value (DOC_STATUS).
    When falling back to value lookup, namespace is used for scoping.
    """
    # Try as ID first, then as value
    result = await TerminologyService.get_terminology(terminology_id=terminology_id)
    if not result:
        result = await TerminologyService.get_terminology(value=terminology_id, namespace=namespace)

    if not result:
        raise HTTPException(status_code=404, detail="Terminology not found")

    return result


@router.put("", response_model=BulkResponse, summary="Update terminologies")
async def update_terminologies(
    items: list[UpdateTerminologyItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> BulkResponse:
    """
    Update one or more terminologies.

    If a value changes, a synonym will be added in the Registry to allow
    lookups by both old and new values.
    """
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
            results.append(BulkResultItem(index=i, status="error", id=item.terminology_id, error=f"Registry error: {str(e)}"))
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
    api_key: str = Depends(require_api_key)
) -> TerminologyDependencies:
    """
    Get dependencies of a terminology.

    Returns information about what depends on this terminology:
    - Templates that reference it via terminology_ref fields

    Use this to check before deactivating a terminology.
    """
    try:
        return await DependencyService.check_terminology_dependencies(terminology_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{terminology_id}/restore", response_model=TerminologyResponse, summary="Restore a terminology")
async def restore_terminology(
    terminology_id: str,
    restore_terms: bool = Query(True, description="Also reactivate inactive terms"),
    api_key: str = Depends(require_api_key)
) -> TerminologyResponse:
    """
    Restore a soft-deleted (inactive) terminology back to active status.

    By default also reactivates all terms that were deactivated with it.
    Set restore_terms=false to restore only the terminology itself.
    """
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
    api_key: str = Depends(require_api_key)
) -> BulkResponse:
    """
    Soft-delete one or more terminologies (set status to inactive).

    All terms in each terminology will also be deactivated.
    Set force=true per item to delete even if templates reference it.
    """
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
                terminology_id=item.id, updated_by=item.updated_by
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
