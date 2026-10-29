"""The templates' side of the synonym lifecycle pass.

A template keeps one Registry entry across its versions and is retired when
no version is active. The pass itself — release retired templates' synonyms,
optionally reclaim active ones' — is shared by every store
(``wip_auth.synonym_lifecycle``); this module supplies the namespace's
template IDs.
"""

from collections.abc import AsyncIterator

from wip_auth.synonym_lifecycle import (
    LifecycleReport,
    batched,
)
from wip_auth.synonym_lifecycle import (
    reconcile_synonym_lifecycle as _reconcile,
)

from ..models.template import Template
from .registry_client import get_registry_client


async def _template_ids(namespace: str, *, retired: bool) -> AsyncIterator[str]:
    """The namespace's template IDs with no active version (retired), or
    with one (active)."""
    cursor = Template.get_motor_collection().aggregate([
        {"$match": {"namespace": namespace}},
        {"$group": {
            "_id": "$template_id",
            "active": {"$max": {"$cond": [{"$eq": ["$status", "active"]}, 1, 0]}},
        }},
        {"$match": {"active": 0 if retired else 1}},
    ])
    async for row in cursor:
        yield row["_id"]


async def reconcile_synonym_lifecycle(
    namespace: str, dry_run: bool = True, revive: bool = False,
) -> LifecycleReport:
    """Release the synonyms of every retired template in a namespace and,
    with revive=True (applied only), reclaim those of every active one."""
    return await _reconcile(
        get_registry_client(), namespace,
        lambda: batched(_template_ids(namespace, retired=True)),
        lambda: batched(_template_ids(namespace, retired=False)),
        dry_run=dry_run, revive=revive,
    )
