"""Validation API endpoints."""

from fastapi import APIRouter, Depends

from ..models.api_models import ValidationRequest, ValidationResponse
from ..services.document_service import get_document_service
from .auth import require_api_key

router = APIRouter(prefix="/validation", tags=["Validation"])


@router.post(
    "/validate",
    response_model=ValidationResponse,
    summary="Validate document without saving",
    description="""
Validate document data against a template without saving.

This is useful for:
- Pre-validation before submission
- Testing document data against templates
- Computing identity hash

Returns validation result with:
- valid: Whether the document passes validation
- errors: List of validation errors
- warnings: Non-blocking warnings
- identity_hash: Computed identity hash (if valid)
- template_version: Template version used
    """
)
async def validate_document(
    request: ValidationRequest,
    _: str = Depends(require_api_key)
):
    """Validate document data without saving."""
    service = get_document_service()
    return await service.validate_document(
        template_id=request.template_id,
        data=request.data,
        namespace=request.namespace,
    )
