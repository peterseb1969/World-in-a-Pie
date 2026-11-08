"""Store every file's allowed_templates as canonical template ids.

A file's ``allowed_templates`` names the templates whose documents may use
it. It used to be stored as submitted — template values — and never
enforced. Writes now resolve each entry to the template's canonical id, and
validation compares ids. A file stored before that, or brought back by a
restore from an older archive, still holds values.

Startup converts them, in the background: each entry is resolved through the
Registry in the file's namespace, as a write would resolve it. An entry that
resolves to nothing is kept, and logged: it then matches no template, so the
file stays restricted rather than becoming open to every template. An empty
list becomes None (no restriction). Idempotent — an id resolves to itself —
and harmless to interrupt.
"""

from __future__ import annotations

import logging
from typing import Any

from wip_auth.resolve import resolve_entity_ids

from ..models.file import File

logger = logging.getLogger("document_store.allowed_templates_ids")


async def convert_allowed_templates_to_ids() -> dict[str, int]:
    """Rewrite every stored allowed_templates entry as a canonical template
    id. Returns how many files were checked, changed, and kept an entry that
    names no template."""
    summary = {"checked": 0, "converted": 0, "unresolved": 0}
    collection = File.get_motor_collection()
    async for row in collection.find(
        {"allowed_templates": {"$ne": None}},
        {"_id": 1, "file_id": 1, "namespace": 1, "allowed_templates": 1},
    ):
        summary["checked"] += 1
        entries: list[str] = list(row.get("allowed_templates") or [])
        update: dict[str, Any]
        if not entries:
            update = {"allowed_templates": None}
        else:
            resolved = await resolve_entity_ids(
                entries, "template", row["namespace"], bypass_cache=True, strict=False,
            )
            missing = [e for e in entries if e not in resolved]
            if missing:
                summary["unresolved"] += 1
                logger.warning(
                    "File %s (%s): allowed_templates %s name no template; kept, so they "
                    "match none and the file stays restricted",
                    row.get("file_id"), row["namespace"], missing,
                )
            ids = list(dict.fromkeys(resolved.get(e, e) for e in entries))
            if ids == entries:
                continue
            update = {"allowed_templates": ids}
        await collection.update_one(
            {"_id": row["_id"], "allowed_templates": row.get("allowed_templates")},
            {"$set": update},
        )
        summary["converted"] += 1
    return summary
