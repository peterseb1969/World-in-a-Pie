"""Maintenance API — namespace-scoped data-repair operations."""

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from wip_auth import UserIdentity, check_namespace_permission, require_api_key

from ..models.api_models import StrictModel
from ..services.identity_migration import rehash_identities
from ..services.synonym_lifecycle import reconcile_synonym_lifecycle
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
    status: Literal["changed", "unchanged", "unresolved", "collision", "error"] = Field(
        description=(
            "changed: the hash differs and the pass can apply it; "
            "unchanged: the stored hash is already right, listed only on an apply "
            "whose Registry re-key for it did not answer unchanged (see "
            "registry_status); "
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
            "Outcome of the Registry re-key for this document: rekeyed or "
            "unchanged on success; not_found, conflict, error, missing or "
            "'error: <reason>' otherwise. Null on a dry run and for documents "
            "the pass did not re-key."
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
        description=(
            "Documents migrated completely: version rows rewritten AND the "
            "Registry re-key succeeded. 0 on a dry run."
        ),
    )
    registry_failed: int = Field(
        default=0,
        description=(
            "Documents whose Registry re-key did not succeed, whether their "
            "rows were rewritten by this run or by an earlier one. Re-run the "
            "apply once the cause is fixed: every apply re-sends the re-key "
            "for documents whose stored hash is already right. 0 on a dry run."
        ),
    )
    registry_repaired: int = Field(
        default=0,
        description=(
            "Documents whose stored hash was already right but whose Registry "
            "claim was not, re-keyed by this run (an earlier apply's Registry "
            "leg had failed). 0 on a dry run."
        ),
    )
    items: list[RehashItemResponse] = Field(
        description=(
            "Every scanned document except the unchanged ones; on an apply, "
            "also unchanged documents whose Registry re-key did not answer "
            "unchanged."
        ),
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
    on one identity, which need vocabulary curation and are never
    applied. An apply is safe to re-run: it re-sends the Registry re-key for
    documents whose stored hash is already right, so a run whose Registry leg
    failed converges; read registry_failed, not only applied. Reporting re-syncs via replay afterwards; the response says so.

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


class ReconcileSynonymLifecycleRequest(StrictModel):
    """One pass aligning the Registry's synonym state with documents' lifecycle."""

    namespace: str
    dry_run: bool = True
    revive: bool = Field(
        default=False,
        description=(
            "Also reclaim the released synonyms of every active document (a "
            "revival whose Registry call failed). Applied only, never on a dry run."
        ),
    )


class LifecycleItemResponse(BaseModel):
    """One document the pass changed, or could not handle."""

    entry_id: str = Field(description="The document_id (its Registry entry ID).")
    status: Literal["released", "reclaimed", "not_reclaimed", "not_found", "error"]
    keys: list[dict[str, Any]] = Field(
        default_factory=list,
        description="The synonyms released, reclaimed, or held by another entry.",
    )
    detail: str | None = None


class ReconcileSynonymLifecycleResponse(BaseModel):
    """The pass report. A dry run and an apply return the same shape."""

    namespace: str
    dry_run: bool
    revive: bool
    retired_scanned: int
    released: int
    active_scanned: int
    reclaimed: int
    not_reclaimed: int
    unchanged: int
    not_found: int
    errors: int
    items: list[LifecycleItemResponse] = Field(
        description="Every document except the unchanged ones.",
    )


@router.post(
    "/reconcile-synonym-lifecycle", response_model=ReconcileSynonymLifecycleResponse,
)
async def reconcile_synonym_lifecycle_endpoint(
    request: ReconcileSynonymLifecycleRequest,
    identity: UserIdentity = Depends(require_api_key),
) -> dict:
    """Release the Registry synonyms of retired documents.

    A soft-deleted document's synonyms — its identity-values synonym and any
    caller-added ones — are released: they stay recorded on its Registry entry
    but neither block a different identity with the same spellings nor
    resolve. Its canonical ID and identity keep resolving, so re-posting it
    still revives it. Deletes and revivals do this as they happen; this pass
    covers documents retired before that, and any delete whose Registry call
    failed. With revive=true it also reclaims the synonyms of active
    documents (a revival whose Registry call failed). Dry-run (the default)
    only reports. Idempotent.

    Requires admin permission on the namespace.
    """
    await check_namespace_permission(identity, request.namespace, "admin")
    report = await reconcile_synonym_lifecycle(
        namespace=request.namespace, dry_run=request.dry_run, revive=request.revive,
    )
    return report.to_dict()
