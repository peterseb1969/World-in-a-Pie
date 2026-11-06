"""Repair file rows whose storage_key is not their file_id.

A file's bytes live under its file id: an upload stores them there, and a
restore uploads the archive's blob there (a fresh restore under the
re-minted id). A fresh restore used to write the row with the SOURCE's
storage_key, so the restored file served the source's bytes on the same
install (and deleting it deleted the source's object), or nothing elsewhere.
Restores now write `storage_key = file_id`; this repairs rows written
before that.

A row is re-pointed only when an object exists under its own file id, so a
row whose bytes were never copied keeps the key it has and is logged by
name instead of losing what it can still serve. Runs at startup, once the
object store is reachable. Idempotent: a repaired install has no such rows.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorCollection

logger = logging.getLogger("document_store.file_storage_keys")


class _Exists(Protocol):
    async def exists(self, storage_key: str) -> bool: ...


async def repair_storage_keys(
    collection: AsyncIOMotorCollection, storage: _Exists
) -> tuple[int, list[str]]:
    """Re-point file rows at the object under their own file id.

    Returns the number of rows repaired and the file ids left as they are
    (no object under their own id)."""
    repaired = 0
    left: list[str] = []
    query: dict[str, Any] = {
        "file_id": {"$exists": True},
        "$expr": {"$ne": ["$storage_key", "$file_id"]},
    }
    async for row in collection.find(query, {"file_id": 1, "storage_key": 1}):
        file_id = row["file_id"]
        if await storage.exists(file_id):
            await collection.update_one(
                {"_id": row["_id"]}, {"$set": {"storage_key": file_id}}
            )
            repaired += 1
        else:
            left.append(file_id)
            logger.warning(
                "File %s has storage_key %r and no object under its own id; left as it is",
                file_id, row.get("storage_key"),
            )
    return repaired, left
