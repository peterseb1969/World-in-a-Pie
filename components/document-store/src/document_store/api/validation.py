"""Validation API endpoints."""

from fastapi import APIRouter, Depends

from wip_auth import UserIdentity, check_namespace_permission, resolve_or_404

from ..models.api_models import (
    BulkValidationRequest,
    BulkValidationResponse,
    ValidationRequest,
    ValidationResponse,
)
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
    identity: UserIdentity = Depends(require_api_key)
):
    """Validate document data without saving."""
    # CASE-384 follow-up — validation reveals template structure +
    # term-reference resolution against a namespace's term corpus. Gate
    # by read on the target namespace.
    if request.namespace:
        await check_namespace_permission(identity, request.namespace, "read")

    request.template_id = await resolve_or_404(
        request.template_id, "template", request.namespace, param_name="template_id"
    )

    service = get_document_service()
    return await service.validate_document(
        template_id=request.template_id,
        data=request.data,
        namespace=request.namespace,
    )


@router.post(
    "/validate-bulk",
    response_model=BulkValidationResponse,
    summary="Validate multiple documents against one template without saving",
    description="""
Validate a batch of documents against a single template, without saving (CASE-419).

Bulk, side-effect-free counterpart to `/validate` — the dry-run validator that
finally got the array form the rest of the write surface already has. All items
validate against ONE `template_id` in ONE `namespace`; the template (and its
nested term/template references) is warmed into cache once, then each item is
validated from cache.

Returns `{ results: [...] }` — one `ValidationResponse` per input item, in the
same order. A document being invalid is reported by that item's `valid: false`
+ `errors` (not as a batch error). An unresolvable `template_id` fails the whole
request with 404, since the batch is single-template.

Zero persistence: no documents created, no versions, no identity-hash registry
side effects (identity hashes are computed locally).
    """
)
async def validate_documents_bulk(
    request: BulkValidationRequest,
    identity: UserIdentity = Depends(require_api_key)
):
    """Validate multiple documents (single template) without saving (CASE-419)."""
    # Same gating as the singular endpoint: validation reveals template
    # structure + term-reference resolution, so require read on the namespace.
    if request.namespace:
        await check_namespace_permission(identity, request.namespace, "read")

    request.template_id = await resolve_or_404(
        request.template_id, "template", request.namespace, param_name="template_id"
    )

    service = get_document_service()
    results = await service.validate_documents_bulk(
        template_id=request.template_id,
        items=request.items,
        namespace=request.namespace,
        template_version=request.template_version,
    )
    return BulkValidationResponse(results=results)
