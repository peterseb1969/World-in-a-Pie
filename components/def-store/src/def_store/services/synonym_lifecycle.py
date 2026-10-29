"""The vocabularies' side of the synonym lifecycle pass.

A terminology is retired when inactive; a term when inactive or deprecated
(a term absorbed by a merge is deprecated too — its synonyms already moved to
the survivor, whose claims the Registry's owner-scoped release never
touches). The pass itself — release retired entities' synonyms, optionally
reclaim active ones' — is shared by every store
(``wip_auth.synonym_lifecycle``); this module supplies the namespace's
terminology and term IDs.
"""

from collections.abc import AsyncIterator

from wip_auth.synonym_lifecycle import (
    LifecycleReport,
    batched,
)
from wip_auth.synonym_lifecycle import (
    reconcile_synonym_lifecycle as _reconcile,
)

from ..models.term import Term
from ..models.terminology import Terminology
from .registry_client import get_registry_client

_RETIRED = ["inactive", "deprecated"]


async def _vocabulary_ids(namespace: str, *, retired: bool) -> AsyncIterator[str]:
    """The namespace's terminology IDs, then term IDs, that are retired (or active)."""
    status = {"$in": _RETIRED} if retired else "active"
    async for t in Terminology.get_motor_collection().find(
        {"namespace": namespace, "status": status}, {"terminology_id": 1},
    ):
        yield t["terminology_id"]
    async for t in Term.get_motor_collection().find(
        {"namespace": namespace, "status": status}, {"term_id": 1},
    ):
        yield t["term_id"]


async def reconcile_synonym_lifecycle(
    namespace: str, dry_run: bool = True, revive: bool = False,
) -> LifecycleReport:
    """Release the synonyms of every retired terminology and term in a
    namespace and, with revive=True (applied only), reclaim those of every
    active one."""
    return await _reconcile(
        get_registry_client(), namespace,
        lambda: batched(_vocabulary_ids(namespace, retired=True)),
        lambda: batched(_vocabulary_ids(namespace, retired=False)),
        dry_run=dry_run, revive=revive,
    )
