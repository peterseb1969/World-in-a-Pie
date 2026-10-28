"""Maintenance API — namespace-scoped data-repair operations."""

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from wip_auth import UserIdentity, check_namespace_permission, require_api_key

from ..models.api_models import StrictModel
from ..services.identity_migration import rehash_identities
from ..services.synonym_migration import requalify_identity_synonyms

router = APIRouter(prefix="/maintenance", tags=["Maintenance"])


class RehashIdentitiesRequest(StrictModel):
    """One explicit identity-hash migration pass over a namespace."""

    namespace: str
    dry_run: bool = True
    template_values: list[str] | None = None


class RehashItemResponse(BaseModel):
    """One document the pass did not leave unchanged."""

    document_id: str
    template_id: str
    template_value: str | None = None
    status: Literal["changed", "unresolved", "collision", "error"] = Field(
        description=(
            "changed: the hash differs and the pass can apply it; "
            "unresolved: no stored resolution to recompute from, left as is; "
            "collision: another document converges on the same identity, "
            "reported and never applied; error: the template version was unreadable."
        ),
    )
    old_hash: str | None = None
    new_hash: str | None = None
    detail: str | None = None
    versions_updated: int = Field(
        default=0, description="Version rows rewritten; 0 on a dry run.",
    )
    registry_status: str | None = Field(
        default=None,
        description=(
            "Outcome of the Registry re-key for this document; null on a dry "
            "run and for documents the pass did not apply."
        ),
    )


class RehashCollisionGroup(BaseModel):
    """Documents that converge on one (template, identity hash)."""

    template_id: str
    identity_hash: str
    document_ids: list[str]


class RehashIdentitiesResponse(BaseModel):
    """The pass report. A dry run and an apply return the same shape."""

    namespace: str
    dry_run: bool
    scanned: int
    changed: int
    unchanged: int
    unresolved: int
    collisions: int
    errors: int
    applied: int = Field(
        description="Documents whose version rows were rewritten; 0 on a dry run.",
    )
    items: list[RehashItemResponse] = Field(
        description="Every scanned document except the unchanged ones.",
    )
    collision_groups: list[RehashCollisionGroup]
    reporting_note: str


@router.post("/rehash-identities", response_model=RehashIdentitiesResponse)
async def rehash_identities_endpoint(
    request: RehashIdentitiesRequest,
    identity: UserIdentity = Depends(require_api_key),
) -> dict:
    """Re-hash referent-identity documents to the resolved-referent feed.

    Term/reference-typed identity fields hash their resolved referent's
    canonical ID; documents written before that rule carry spelling-fed
    hashes. This pass recomputes each document's hash from its stored
    resolutions, updates every version row, and re-keys the Registry's
    composite-key claims. Dry-run (the default) only reports — read it
    first: collision groups are formerly-forked documents now converging
    on one identity, which need curation (archive or merge) and are never
    applied. Reporting re-syncs via replay afterwards; the response says so.

    Requires admin permission on the namespace.
    """
    await check_namespace_permission(identity, request.namespace, "admin")
    report = await rehash_identities(
        namespace=request.namespace,
        dry_run=request.dry_run,
        template_values=request.template_values,
    )
    return report.to_dict()


class RequalifySynonymsRequest(StrictModel):
    """One pass re-keying a namespace's identity-values synonyms."""

    namespace: str
    dry_run: bool = True


class RequalifyItemResponse(BaseModel):
    """One document the pass did not leave unchanged."""

    document_id: str
    template_value: str | None = None
    status: Literal["requalified", "no_entry", "error"]
    bare: dict[str, Any] | None = Field(
        default=None, description="The bare synonym that is replaced.",
    )
    qualified: dict[str, Any] | None = Field(
        default=None, description="The template-qualified synonym that replaces it.",
    )
    detail: str | None = None


class RequalifyIdentitySynonymsResponse(BaseModel):
    """The pass report. A dry run and an apply return the same shape."""

    namespace: str
    dry_run: bool
    scanned: int
    requalified: int
    unchanged: int
    no_entry: int
    errors: int
    items: list[RequalifyItemResponse] = Field(
        description="Every scanned document except the unchanged ones.",
    )


@router.post(
    "/requalify-identity-synonyms", response_model=RequalifyIdentitySynonymsResponse,
)
async def requalify_identity_synonyms_endpoint(
    request: RequalifySynonymsRequest,
    identity: UserIdentity = Depends(require_api_key),
) -> dict:
    """Re-key documents' identity-values synonyms to the template-qualified form.

    A document's identity spellings were registered as a bare synonym (e.g.
    ``{title: "X"}``), which claimed the spelling for every template in the
    namespace. New documents register ``{"@template": <template value>,
    title: "X"}``; this pass re-keys existing ones: add the qualified
    synonym, remove the bare one (releasing its claim). Dry-run (the
    default) only reports. Idempotent.

    Requires admin permission on the namespace.
    """
    await check_namespace_permission(identity, request.namespace, "admin")
    report = await requalify_identity_synonyms(
        namespace=request.namespace, dry_run=request.dry_run,
    )
    return report.to_dict()
