"""Validation API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from wip_auth import UserIdentity, check_namespace_permission, resolve_or_404

from ..models.api_models import (
    BulkValidationRequest,
    BulkValidationResponse,
    CandidateValidationItem,
    CandidateValidationRequest,
    CandidateValidationResponse,
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
    # Validation reveals template structure + term-reference resolution
    # against a namespace's term corpus, so the read gate is unconditional.
    # namespace is a required field — a conditional `if request.namespace:`
    # gate's only reachable effect was letting an empty-string namespace
    # skip enforcement entirely.
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
Validate a batch of documents against a single template, without saving.

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
    # Same gating as the singular endpoint: unconditional — namespace is
    # required, and a falsy-check gate lets an empty string skip enforcement.
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


@router.post(
    "/validate-candidate",
    response_model=CandidateValidationResponse,
    summary="Validate documents against an inline candidate template definition",
    description="""
The what-if dry-run for schema evolution: "would my documents still validate
against this draft next version?" — answered before the version exists.

The candidate definition is used inline: nothing is created, cached, or
registered, and no draft version pollutes the template's version catalog.
Provide either explicit `documents` payloads, or `sample_template_id` to run
the candidate against the most recently updated active documents of an
existing template (`sample_limit`, default 100, max 500).

Reference values inside the candidate (terminology_ref etc.) should be
canonical IDs or resolvable synonyms — an unresolvable reference surfaces as
a per-document validation error, exactly as it would on a real write.

Declared renames are honored: a candidate carrying `renames`
(`{new_field: old_field}`) validates each document as-if re-keyed — the
same semantics an applied migration uses — so a declared rename does not
false-fail as an unknown old field plus a missing new one.
""",
)
async def validate_candidate(
    request: CandidateValidationRequest,
    identity: UserIdentity = Depends(require_api_key),
):
    """Validate documents against a candidate definition without creating it."""
    await check_namespace_permission(identity, request.namespace, "read")

    if (request.documents is None) == (request.sample_template_id is None):
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one of 'documents' or 'sample_template_id'",
        )

    sample_id = None
    if request.sample_template_id:
        sample_id = await resolve_or_404(
            request.sample_template_id, "template", request.namespace,
            param_name="sample_template_id",
        )

    service = get_document_service()
    pairs = await service.validate_candidate(
        template_definition=request.template_definition,
        namespace=request.namespace,
        documents=request.documents,
        sample_template_id=sample_id,
        sample_limit=request.sample_limit,
    )
    items = [
        CandidateValidationItem(index=i, document_id=doc_id, validation=v)
        for i, (doc_id, v) in enumerate(pairs)
    ]
    valid_count = sum(1 for it in items if it.validation.valid)
    return CandidateValidationResponse(
        total=len(items),
        valid_count=valid_count,
        invalid_count=len(items) - valid_count,
        results=items,
    )
