"""Maintenance API — namespace-scoped data-repair operations."""

from fastapi import APIRouter, Depends

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


@router.post("/rehash-identities")
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


@router.post("/requalify-identity-synonyms")
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
