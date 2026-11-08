"""One-off: the read grants a runtime key's namespace list used to stand in for.

A runtime key's ``namespaces`` list once gave read on every namespace it named
with no grant. It is a scope now and gives nothing; key creation writes a read
grant instead. Keys created before that hold no grant where they relied on the
list, so this writes one read grant per such (key, namespace), for namespaces
that exist.

It runs once per install, recorded by a marker: run again, it would hand back
read grants that a later namespace deletion purged or an admin revoked, which
is exactly the access the change ends. Keys managed by wip-deploy are left
alone (the key-file import writes their grants).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from ..models.api_key import StoredAPIKey
from ..models.grant import NamespaceGrant
from ..models.namespace import Namespace

logger = logging.getLogger("registry.key_scope_grants")

COLLECTION = "registry_migrations"
_MARKER_ID = "key_scope_read_grants"


def _markers() -> Any:
    return StoredAPIKey.get_motor_collection().database[COLLECTION]


async def reset_marker_for_tests() -> None:
    await _markers().delete_one({"_id": _MARKER_ID})


async def backfill_scope_read_grants() -> int:
    """Write the missing read grants, once. Returns how many were written."""
    if await _markers().find_one({"_id": _MARKER_ID}) is not None:
        return 0
    existing = {ns.prefix for ns in await Namespace.find({}).to_list()}
    written = 0
    async for key in StoredAPIKey.find({"managed_by": None, "namespaces": {"$ne": None}}):
        for prefix in key.namespaces or []:
            if prefix not in existing:
                continue
            held = await NamespaceGrant.find_one({
                "namespace": prefix, "subject_type": "api_key",
                "subject": {"$in": [key.name, f"apikey:{key.name}"]},
            })
            if held is not None:  # any grant, orphaned included, is the decision
                continue
            await NamespaceGrant(
                namespace=prefix, subject=key.name, subject_type="api_key",
                permission="read", granted_by="system:key-scope-backfill",
            ).create()
            written += 1
    await _markers().update_one(
        {"_id": _MARKER_ID},
        {"$setOnInsert": {"done_at": datetime.now(UTC), "grants_written": written}},
        upsert=True,
    )
    if written:
        logger.info("Wrote %d read grant(s) that runtime keys' namespace lists stood in for", written)
    return written
