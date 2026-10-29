"""Maintenance API — namespace-scoped repair operations on vocabularies."""

from fastapi import APIRouter, Depends

from wip_auth import UserIdentity, check_namespace_permission

from ..models.api_models import StrictModel
from ..services.synonym_lifecycle import reconcile_synonym_lifecycle
from .auth import require_api_key

router = APIRouter(prefix="/maintenance", tags=["Maintenance"])


class ReconcileSynonymLifecycleRequest(StrictModel):
    """One pass aligning the Registry's synonym state with vocabularies' lifecycle."""

    namespace: str
    dry_run: bool = True
    revive: bool = False


@router.post("/reconcile-synonym-lifecycle")
async def reconcile_synonym_lifecycle_endpoint(
    request: ReconcileSynonymLifecycleRequest,
    identity: UserIdentity = Depends(require_api_key),
) -> dict:
    """Release the Registry synonyms of retired terminologies and terms.

    An inactive terminology, or an inactive or deprecated term, is retired:
    its caller-added synonyms are released — they stay recorded on its
    Registry entry but neither block another entity from claiming the key
    nor resolve. Its canonical ID and value keys keep resolving, so it stays
    addressable by value. Deprecations, deletions and restores do this as
    they happen; this pass covers entities retired before that, and any
    whose Registry call failed. With revive=true it also reclaims the
    synonyms of active ones (applied only, never on a dry run). Dry-run (the
    default) only reports. Idempotent.

    Requires admin permission on the namespace.
    """
    await check_namespace_permission(identity, request.namespace, "admin")
    report = await reconcile_synonym_lifecycle(
        namespace=request.namespace, dry_run=request.dry_run, revive=request.revive,
    )
    return report.to_dict()
