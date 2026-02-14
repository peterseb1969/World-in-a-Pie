"""API endpoints for term management."""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends

from ..models.api_models import (
    CreateTermRequest,
    UpdateTermRequest,
    DeprecateTermRequest,
    TermResponse,
    TermListResponse,
    BulkCreateTermRequest,
    BulkOperationResponse,
    ValidateValueRequest,
    ValidateValueResponse,
    BulkValidateRequest,
    BulkValidateResponse,
)
from ..models.terminology import Terminology
from ..services.terminology_service import TerminologyService
from ..services.registry_client import RegistryError
from .auth import require_api_key

router = APIRouter(tags=["Terms"])


# =============================================================================
# TERM CRUD
# =============================================================================

@router.post(
    "/terminologies/{terminology_id}/terms",
    response_model=TermResponse,
    status_code=201,
    summary="Create a term"
)
async def create_term(
    terminology_id: str,
    request: CreateTermRequest,
    api_key: str = Depends(require_api_key)
) -> TermResponse:
    """
    Create a new term in a terminology.

    Namespace is inherited from the parent terminology.
    The term will be registered with the Registry service to get
    a unique ID.
    """
    try:
        return await TerminologyService.create_term(terminology_id, request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=502, detail=f"Registry error: {str(e)}")


@router.post(
    "/terminologies/{terminology_id}/terms/bulk",
    response_model=BulkOperationResponse,
    status_code=201,
    summary="Create multiple terms"
)
async def create_terms_bulk(
    terminology_id: str,
    request: BulkCreateTermRequest,
    batch_size: int = Query(
        1000,
        description="Number of terms per MongoDB batch (default 1000)"
    ),
    registry_batch_size: int = Query(
        100,
        description="Number of terms per registry HTTP call (default 100). "
        "Reduce if experiencing timeouts on large imports."
    ),
    api_key: str = Depends(require_api_key)
) -> BulkOperationResponse:
    """
    Create multiple terms in a terminology at once.

    Namespace is inherited from the parent terminology.
    Useful for importing terms or seeding data.

    For very large imports (100k+ terms), you may need to tune the batch sizes:
    - `batch_size`: Controls MongoDB batch size (default 1000)
    - `registry_batch_size`: Controls registry HTTP call batch size (default 100)

    If you experience timeouts, try reducing `registry_batch_size` to 50 or lower.
    """
    try:
        results = await TerminologyService.create_terms_bulk(
            terminology_id=terminology_id,
            terms=request.terms,
            created_by=request.created_by,
            batch_size=batch_size,
            registry_batch_size=registry_batch_size,
        )
        succeeded = sum(1 for r in results if r.status in ("created", "updated"))
        failed = sum(1 for r in results if r.status == "error")

        return BulkOperationResponse(
            results=results,
            total=len(results),
            succeeded=succeeded,
            failed=failed
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=502, detail=f"Registry error: {str(e)}")


@router.get(
    "/terminologies/{terminology_id}/terms",
    response_model=TermListResponse,
    summary="List terms in a terminology"
)
async def list_terms(
    terminology_id: str,
    namespace: Optional[str] = Query(default=None, description="Namespace to query (omit for all)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=500, description="Items per page"),
    status: Optional[str] = Query(None, description="Filter by status"),
    search: Optional[str] = Query(None, description="Search in value, aliases"),
    api_key: str = Depends(require_api_key)
) -> TermListResponse:
    """List terms in a terminology with pagination."""
    # Get terminology info
    terminology = await Terminology.find_one({"terminology_id": terminology_id})
    if not terminology:
        # Try by value
        if namespace:
            terminology = await Terminology.find_one({"namespace": namespace, "value": terminology_id})
        else:
            terminology = await Terminology.find_one({"value": terminology_id})
        if not terminology:
            raise HTTPException(status_code=404, detail="Terminology not found")

    terms, total = await TerminologyService.list_terms(
        terminology_id=terminology.terminology_id,
        status=status,
        page=page,
        page_size=page_size,
        search=search,
        namespace=namespace
    )

    return TermListResponse(
        items=terms,
        total=total,
        page=page,
        page_size=page_size,
        terminology_id=terminology.terminology_id,
        terminology_value=terminology.value
    )


@router.get(
    "/terms/{term_id}",
    response_model=TermResponse,
    summary="Get a term by ID"
)
async def get_term(
    term_id: str,
    api_key: str = Depends(require_api_key)
) -> TermResponse:
    """Get a term by its ID."""
    result = await TerminologyService.get_term(term_id=term_id)
    if not result:
        raise HTTPException(status_code=404, detail="Term not found")
    return result


@router.put(
    "/terms/{term_id}",
    response_model=TermResponse,
    summary="Update a term"
)
async def update_term(
    term_id: str,
    request: UpdateTermRequest,
    api_key: str = Depends(require_api_key)
) -> TermResponse:
    """Update a term."""
    try:
        result = await TerminologyService.update_term(term_id, request)
        if not result:
            raise HTTPException(status_code=404, detail="Term not found")
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RegistryError as e:
        raise HTTPException(status_code=502, detail=f"Registry error: {str(e)}")


@router.post(
    "/terms/{term_id}/deprecate",
    response_model=TermResponse,
    summary="Deprecate a term"
)
async def deprecate_term(
    term_id: str,
    request: DeprecateTermRequest,
    api_key: str = Depends(require_api_key)
) -> TermResponse:
    """
    Deprecate a term.

    Deprecated terms are kept for historical data but marked as deprecated.
    Optionally specify a replacement term.
    """
    result = await TerminologyService.deprecate_term(term_id, request)
    if not result:
        raise HTTPException(status_code=404, detail="Term not found")
    return result


@router.delete(
    "/terms/{term_id}",
    summary="Delete a term"
)
async def delete_term(
    term_id: str,
    updated_by: Optional[str] = Query(None, description="User performing deletion"),
    api_key: str = Depends(require_api_key)
) -> dict:
    """Soft-delete a term (set status to inactive)."""
    success = await TerminologyService.delete_term(
        term_id=term_id,
        updated_by=updated_by
    )
    if not success:
        raise HTTPException(status_code=404, detail="Term not found")

    return {"status": "deleted", "term_id": term_id}


# =============================================================================
# VALIDATION
# =============================================================================

@router.post(
    "/validate",
    response_model=ValidateValueResponse,
    summary="Validate a value"
)
async def validate_value(
    request: ValidateValueRequest,
    api_key: str = Depends(require_api_key)
) -> ValidateValueResponse:
    """
    Validate a value against a terminology.

    Returns whether the value is valid and the matching term if found.
    Also provides suggestions for close matches.
    """
    if not request.terminology_id and not request.terminology_value:
        raise HTTPException(
            status_code=400,
            detail="Must provide terminology_id or terminology_value"
        )

    # Get terminology for response
    if request.terminology_id:
        terminology = await Terminology.find_one({"terminology_id": request.terminology_id})
    else:
        terminology = await Terminology.find_one({"value": request.terminology_value})

    if not terminology:
        return ValidateValueResponse(
            valid=False,
            terminology_id=request.terminology_id or "",
            terminology_value=request.terminology_value or "",
            value=request.value,
            error="Terminology not found"
        )

    is_valid, matched_term, matched_via, suggestion = await TerminologyService.validate_value(
        terminology_id=terminology.terminology_id,
        value=request.value
    )

    return ValidateValueResponse(
        valid=is_valid,
        terminology_id=terminology.terminology_id,
        terminology_value=terminology.value,
        value=request.value,
        matched_term=TerminologyService._to_term_response(matched_term) if matched_term else None,
        matched_via=matched_via,
        suggestion=TerminologyService._to_term_response(suggestion) if suggestion else None
    )


@router.post(
    "/validate/bulk",
    response_model=BulkValidateResponse,
    summary="Validate multiple values"
)
async def validate_values_bulk(
    request: BulkValidateRequest,
    api_key: str = Depends(require_api_key)
) -> BulkValidateResponse:
    """Validate multiple values at once."""
    results = []
    valid_count = 0
    invalid_count = 0

    for item in request.items:
        # Get terminology
        if item.terminology_id:
            terminology = await Terminology.find_one({"terminology_id": item.terminology_id})
        elif item.terminology_value:
            terminology = await Terminology.find_one({"value": item.terminology_value})
        else:
            results.append(ValidateValueResponse(
                valid=False,
                terminology_id="",
                terminology_value="",
                value=item.value,
                error="Must provide terminology_id or terminology_value"
            ))
            invalid_count += 1
            continue

        if not terminology:
            results.append(ValidateValueResponse(
                valid=False,
                terminology_id=item.terminology_id or "",
                terminology_value=item.terminology_value or "",
                value=item.value,
                error="Terminology not found"
            ))
            invalid_count += 1
            continue

        is_valid, matched_term, matched_via, suggestion = await TerminologyService.validate_value(
            terminology_id=terminology.terminology_id,
            value=item.value
        )

        results.append(ValidateValueResponse(
            valid=is_valid,
            terminology_id=terminology.terminology_id,
            terminology_value=terminology.value,
            value=item.value,
            matched_term=TerminologyService._to_term_response(matched_term) if matched_term else None,
            matched_via=matched_via,
            suggestion=TerminologyService._to_term_response(suggestion) if suggestion else None
        ))

        if is_valid:
            valid_count += 1
        else:
            invalid_count += 1

    return BulkValidateResponse(
        results=results,
        total=len(results),
        valid_count=valid_count,
        invalid_count=invalid_count
    )
