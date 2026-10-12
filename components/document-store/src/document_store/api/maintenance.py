"""Maintenance API — namespace-scoped data-repair operations."""

from fastapi import APIRouter, Depends

from wip_auth import UserIdentity, check_namespace_permission, require_api_key

from ..models.api_models import StrictModel
from ..services.identity_migration import rehash_identities

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
