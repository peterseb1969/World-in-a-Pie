"""Validation API endpoints for the Def-Store service."""

from fastapi import APIRouter, HTTPException

from ..models.api_models import (
    ValidateValueRequest,
    ValidateValueResponse,
    BulkValidateRequest,
    BulkValidateResponse,
    TermResponse,
)
from ..services.terminology_service import TerminologyService

router = APIRouter(prefix="/validation", tags=["Validation"])


def term_to_response(term) -> TermResponse:
    """Convert a Term document to a TermResponse."""
    return TermResponse(
        term_id=term.term_id,
        terminology_id=term.terminology_id,
        terminology_value=term.terminology_value,
        value=term.value,
        aliases=term.aliases,
        label=term.label or term.value,
        description=term.description,
        sort_order=term.sort_order,
        parent_term_id=term.parent_term_id,
        translations=term.translations,
        metadata=term.metadata,
        status=term.status,
        deprecated_reason=term.deprecated_reason,
        replaced_by_term_id=term.replaced_by_term_id,
        created_at=term.created_at,
        created_by=term.created_by,
        updated_at=term.updated_at,
        updated_by=term.updated_by,
    )


@router.post("/validate", response_model=ValidateValueResponse)
async def validate_value(request: ValidateValueRequest) -> ValidateValueResponse:
    """
    Validate a single value against a terminology.

    Provide either terminology_id or terminology_value (not both).
    """
    if not request.terminology_id and not request.terminology_value:
        raise HTTPException(
            status_code=400,
            detail="Either terminology_id or terminology_value is required"
        )

    try:
        is_valid, matched_term, matched_via, suggestion = await TerminologyService.validate_value(
            terminology_id=request.terminology_id,
            terminology_value=request.terminology_value,
            value=request.value
        )

        # Get terminology info for response
        if request.terminology_id:
            terminology = await TerminologyService.get_terminology(terminology_id=request.terminology_id)
        else:
            terminology = await TerminologyService.get_terminology(value=request.terminology_value)

        if not terminology:
            raise HTTPException(status_code=404, detail="Terminology not found")

        return ValidateValueResponse(
            valid=is_valid,
            terminology_id=terminology.terminology_id,
            terminology_value=terminology.value,
            value=request.value,
            matched_term=term_to_response(matched_term) if matched_term else None,
            matched_via=matched_via,
            suggestion=term_to_response(suggestion) if suggestion else None,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/validate-bulk", response_model=BulkValidateResponse)
async def validate_bulk(request: BulkValidateRequest) -> BulkValidateResponse:
    """
    Validate multiple values against terminologies.
    """
    results = []
    valid_count = 0
    invalid_count = 0

    for item in request.items:
        try:
            if not item.terminology_id and not item.terminology_value:
                results.append(ValidateValueResponse(
                    valid=False,
                    terminology_id="",
                    terminology_value="",
                    value=item.value,
                    error="Either terminology_id or terminology_value is required"
                ))
                invalid_count += 1
                continue

            is_valid, matched_term, matched_via, suggestion = await TerminologyService.validate_value(
                terminology_id=item.terminology_id,
                terminology_value=item.terminology_value,
                value=item.value
            )

            # Get terminology info
            if item.terminology_id:
                terminology = await TerminologyService.get_terminology(terminology_id=item.terminology_id)
            else:
                terminology = await TerminologyService.get_terminology(value=item.terminology_value)

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

            results.append(ValidateValueResponse(
                valid=is_valid,
                terminology_id=terminology.terminology_id,
                terminology_value=terminology.value,
                value=item.value,
                matched_term=term_to_response(matched_term) if matched_term else None,
                matched_via=matched_via,
                suggestion=term_to_response(suggestion) if suggestion else None,
            ))

            if is_valid:
                valid_count += 1
            else:
                invalid_count += 1

        except ValueError as e:
            results.append(ValidateValueResponse(
                valid=False,
                terminology_id=item.terminology_id or "",
                terminology_value=item.terminology_value or "",
                value=item.value,
                error=str(e)
            ))
            invalid_count += 1

    return BulkValidateResponse(
        results=results,
        total=len(results),
        valid_count=valid_count,
        invalid_count=invalid_count
    )
