"""Maintenance API — namespace-scoped repair operations on stored templates."""

from fastapi import APIRouter, Depends

from wip_auth import UserIdentity, check_namespace_permission

from ..models.api_models import StrictModel
from ..services.endpoint_heal import heal_endpoints
from ..services.synonym_lifecycle import reconcile_synonym_lifecycle
from .auth import require_api_key

router = APIRouter(
    prefix="/maintenance",
    tags=["Maintenance"],
    dependencies=[Depends(require_api_key)],
)


class HealEndpointsRequest(StrictModel):
    """One idempotent heal pass over a namespace's edge-type endpoints."""

    namespace: str
    dry_run: bool = True


@router.post("/heal-endpoints")
async def heal_endpoints_endpoint(
    request: HealEndpointsRequest,
    identity: UserIdentity = Depends(require_api_key),
) -> dict:
    """Fill null `resolved` halves on the namespace's edge-type endpoints.

    Edge types declared before endpoints carried a resolved half serve
    `resolved: null` on every entry until something rewrites them — and the
    natural rewrite triggers (widen, activation, restore repair) rarely touch
    an established edge type. This pass resolves each null entry's
    `lookup_value` through the Registry in the template's own namespace and
    persists it in place; entries that no longer resolve are reported and
    left null (they keep failing closed where an exact id is required).
    Dry-run by default — the report lists what would change, per template.

    Requires admin permission on the namespace.
    """
    await check_namespace_permission(identity, request.namespace, "admin")
    report = await heal_endpoints(
        namespace=request.namespace, dry_run=request.dry_run,
    )
    return report.to_dict()


class ReconcileSynonymLifecycleRequest(StrictModel):
    """One pass aligning the Registry's synonym state with templates' lifecycle."""

    namespace: str
    dry_run: bool = True
    revive: bool = False


@router.post("/reconcile-synonym-lifecycle")
async def reconcile_synonym_lifecycle_endpoint(
    request: ReconcileSynonymLifecycleRequest,
    identity: UserIdentity = Depends(require_api_key),
) -> dict:
    """Release the Registry synonyms of retired templates.

    A template whose versions are all inactive is retired: its synonyms are
    released — they stay recorded on its Registry entry but neither block
    another template from claiming the key nor resolve. Its canonical ID and
    its {ns, type, value} synonym keep resolving, so it stays addressable by
    value. Deactivations and reactivations do this as they happen; this pass
    covers templates retired before that, and any whose Registry call failed.
    With revive=true it also reclaims the synonyms of templates with an
    active version (applied only, never on a dry run). Dry-run (the default)
    only reports. Idempotent.

    Requires admin permission on the namespace.
    """
    await check_namespace_permission(identity, request.namespace, "admin")
    report = await reconcile_synonym_lifecycle(
        namespace=request.namespace, dry_run=request.dry_run, revive=request.revive,
    )
    return report.to_dict()
