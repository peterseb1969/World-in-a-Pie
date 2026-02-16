"""API endpoints for terminology management."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends

from ..models.api_models import (
    CreateTerminologyRequest,
    UpdateTerminologyRequest,
    TerminologyResponse,
    TerminologyListResponse,
)
from ..services.terminology_service import TerminologyService
from ..services.registry_client import RegistryError
from ..services.dependency_service import DependencyService, TerminologyDependencies
from .auth import require_api_key

router = APIRouter(prefix="/terminologies", tags=["Terminologies"])


@router.post("", response_model=TerminologyResponse, status_code=201, summary="Create a terminology")
async def create_terminology(
    request: CreateTerminologyRequest,
    api_key: str = Depends(require_api_key)
) -> TerminologyResponse:
    """
    Create a new terminology (controlled vocabulary).

    The terminology will be registered with the Registry service to get
    a unique ID. Namespace is specified in the request body (default: "wip").
    """
    try:
        return await TerminologyService.create_terminology(request, namespace=request.namespace)
    except ValueError as e:
        msg = str(e)
        status = 409 if "already exists" in msg else 400
        raise HTTPException(status_code=status, detail=msg)
    except RegistryError as e:
        raise HTTPException(status_code=502, detail=f"Registry error: {str(e)}")


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
        page_size=page_size
    )


@router.get("/by-value/{value}", response_model=TerminologyResponse, summary="Get a terminology by value")
async def get_terminology_by_value(
    value: str,
    namespace: Optional[str] = Query(default=None, description="Namespace to search in (omit for all)"),
    api_key: str = Depends(require_api_key)
) -> TerminologyResponse:
    """Get a terminology by its value (e.g., DOC_STATUS)."""
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


@router.put("/{terminology_id}", response_model=TerminologyResponse, summary="Update a terminology")
async def update_terminology(
    terminology_id: str,
    request: UpdateTerminologyRequest,
    api_key: str = Depends(require_api_key)
) -> TerminologyResponse:
    """
    Update a terminology.

    If the value changes, a synonym will be added in the Registry to allow
    lookups by both old and new values.
    """
    try:
        result = await TerminologyService.update_terminology(terminology_id, request)
        if not result:
            raise HTTPException(status_code=404, detail="Terminology not found")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=502, detail=f"Registry error: {str(e)}")


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


@router.delete("/{terminology_id}", summary="Delete a terminology")
async def delete_terminology(
    terminology_id: str,
    updated_by: Optional[str] = Query(None, description="User performing deletion"),
    force: bool = Query(False, description="Force deletion even if templates reference it"),
    api_key: str = Depends(require_api_key)
) -> dict:
    """
    Soft-delete a terminology (set status to inactive).

    All terms in the terminology will also be deactivated.

    If templates reference this terminology, returns a warning unless force=true.
    Use GET /{terminology_id}/dependencies to check dependencies first.
    """
    try:
        # Check dependencies first
        deps = await DependencyService.check_terminology_dependencies(terminology_id)

        # Warn if templates reference this (unless force)
        if deps.template_count > 0 and not force:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Terminology has dependent templates",
                    "message": deps.warning_message,
                    "template_count": deps.template_count,
                    "templates": deps.templates,
                    "hint": "Use force=true to deactivate anyway"
                }
            )

        success = await TerminologyService.delete_terminology(
            terminology_id=terminology_id,
            updated_by=updated_by
        )
        if not success:
            raise HTTPException(status_code=404, detail="Terminology not found")

        response = {"status": "deleted", "terminology_id": terminology_id}
        if deps.template_count > 0:
            response["warning"] = f"{deps.template_count} templates still reference this terminology"

        return response
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
