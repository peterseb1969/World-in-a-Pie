"""API endpoints for term management."""

import contextlib
import math

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from wip_auth import (
    EntityNotFoundError,
    check_namespace_permission,
    get_current_identity,
    resolve_accessible_namespaces,
    resolve_entity_id,
)

from ..models.api_models import (
    BulkResponse,
    BulkResultItem,
    BulkValidateRequest,
    BulkValidateResponse,
    CreateTermRequest,
    DeleteItem,
    DeprecateTermItem,
    TermListResponse,
    TermResponse,
    UpdateTermItem,
    ValidateValueRequest,
    ValidateValueResponse,
)
from ..models.terminology import Terminology
from ..services.registry_client import RegistryError
from ..services.terminology_service import TerminologyService
from .auth import require_api_key

router = APIRouter(tags=["Terms"])


# =============================================================================
# TERM CRUD
# =============================================================================

@router.post(
    "/terminologies/{terminology_id}/terms",
    response_model=BulkResponse,
    summary="Create terms"
)
async def create_terms(
    terminology_id: str,
    items: list[CreateTermRequest] = Body(...),
    namespace: str | None = Query(
        default=None,
        description="Namespace for synonym resolution (inferred from terminology if omitted)"
    ),
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
) -> BulkResponse:
    """
    Create one or more terms in a terminology.

    Namespace is inherited from the parent terminology.
    For single items, uses direct creation. For multiple items, uses
    optimized batch operations.

    For very large imports (100k+ terms), you may need to tune the batch sizes:
    - `batch_size`: Controls MongoDB batch size (default 1000)
    - `registry_batch_size`: Controls registry HTTP call batch size (default 100)
    """
    # Resolve terminology_id synonym (e.g., "STATUS" → UUID)
    with contextlib.suppress(EntityNotFoundError):
        terminology_id = await resolve_entity_id(
            terminology_id, "terminology", namespace
        )

    # Look up terminology to get namespace for permission check
    term_parent = await Terminology.find_one({"terminology_id": terminology_id})
    if term_parent:
        identity = get_current_identity()
        await check_namespace_permission(identity, term_parent.namespace, "write")

    if len(items) == 1:
        # Single item — use direct create path
        try:
            result = await TerminologyService.create_term(terminology_id, items[0])
            results = [BulkResultItem(index=0, status="created", id=result.term_id, value=items[0].value)]
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise HTTPException(status_code=404, detail=msg) from None
            results = [BulkResultItem(index=0, status="error", value=items[0].value, error=msg)]
        except RegistryError as e:
            results = [BulkResultItem(index=0, status="error", value=items[0].value, error=f"Registry error: {e!s}")]
    else:
        # Bulk path — uses batch Registry calls and insert_many
        try:
            results = await TerminologyService.create_terms_bulk(
                terminology_id=terminology_id,
                terms=items,
                batch_size=batch_size,
                registry_batch_size=registry_batch_size,
            )
        except ValueError as e:
            msg = str(e)
            if "not found" in msg:
                raise HTTPException(status_code=404, detail=msg) from None
            raise HTTPException(status_code=400, detail=msg) from None
        except RegistryError as e:
            raise HTTPException(status_code=502, detail=f"Registry error: {e!s}") from e

    succeeded = sum(1 for r in results if r.status != "error")
    failed = sum(1 for r in results if r.status == "error")
    return BulkResponse(results=results, total=len(results), succeeded=succeeded, failed=failed)


@router.get(
    "/terminologies/{terminology_id}/terms",
    response_model=TermListResponse,
    summary="List terms in a terminology"
)
async def list_terms(
    terminology_id: str,
    namespace: str | None = Query(default=None, description="Namespace to query (omit for all)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    status: str | None = Query(None, description="Filter by status"),
    search: str | None = Query(None, description="Search in value, aliases"),
    api_key: str = Depends(require_api_key)
) -> TermListResponse:
    """List terms in a terminology with pagination."""
    identity = get_current_identity()
    allowed_namespaces = None
    if namespace:
        await check_namespace_permission(identity, namespace, "read")
    else:
        allowed_namespaces = await resolve_accessible_namespaces(identity)

    # Resolve terminology_id synonym (e.g., "STATUS" → UUID)
    with contextlib.suppress(EntityNotFoundError):
        terminology_id = await resolve_entity_id(
            terminology_id, "terminology", namespace
        )

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
        namespace=namespace,
        allowed_namespaces=allowed_namespaces,
    )

    return TermListResponse(
        items=terms,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total > 0 else 0,
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
    namespace: str | None = Query(None, description="Namespace for synonym resolution"),
    api_key: str = Depends(require_api_key)
) -> TermResponse:
    """Get a term by its ID or synonym (e.g., "STATUS:approved")."""
    # Resolve synonym — supports colon notation for terms
    with contextlib.suppress(EntityNotFoundError):
        term_id = await resolve_entity_id(term_id, "term", namespace)

    result = await TerminologyService.get_term(term_id=term_id)
    if not result:
        raise HTTPException(status_code=404, detail="Term not found")
    return result


@router.put(
    "/terms",
    response_model=BulkResponse,
    summary="Update terms"
)
async def update_terms(
    items: list[UpdateTermItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> BulkResponse:
    """Update one or more terms."""
    results = []
    for i, item in enumerate(items):
        try:
            result = await TerminologyService.update_term(item.term_id, item)
            if not result:
                results.append(BulkResultItem(index=i, status="error", id=item.term_id, error="Term not found"))
            else:
                results.append(BulkResultItem(index=i, status="updated", id=item.term_id))
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.term_id, error=str(e)))
        except RegistryError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.term_id, error=f"Registry error: {e!s}"))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.post(
    "/terms/deprecate",
    response_model=BulkResponse,
    summary="Deprecate terms"
)
async def deprecate_terms(
    items: list[DeprecateTermItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> BulkResponse:
    """
    Deprecate one or more terms.

    Deprecated terms are kept for historical data but marked as deprecated.
    Optionally specify a replacement term per item.
    """
    results = []
    for i, item in enumerate(items):
        try:
            result = await TerminologyService.deprecate_term(item.term_id, item)
            if not result:
                results.append(BulkResultItem(index=i, status="error", id=item.term_id, error="Term not found"))
            else:
                results.append(BulkResultItem(index=i, status="updated", id=item.term_id))
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.term_id, error=str(e)))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


@router.delete(
    "/terms",
    response_model=BulkResponse,
    summary="Delete terms"
)
async def delete_terms(
    items: list[DeleteItem] = Body(...),
    api_key: str = Depends(require_api_key)
) -> BulkResponse:
    """Soft-delete one or more terms (set status to inactive)."""
    results = []
    for i, item in enumerate(items):
        try:
            success = await TerminologyService.delete_term(
                term_id=item.id, updated_by=item.updated_by,
                hard_delete=item.hard_delete,
            )
            if not success:
                results.append(BulkResultItem(index=i, status="error", id=item.id, error="Term not found"))
            else:
                results.append(BulkResultItem(index=i, status="deleted", id=item.id))
        except ValueError as e:
            results.append(BulkResultItem(index=i, status="error", id=item.id, error=str(e)))
    return BulkResponse(
        results=results, total=len(items),
        succeeded=sum(1 for r in results if r.status != "error"),
        failed=sum(1 for r in results if r.status == "error"),
    )


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
