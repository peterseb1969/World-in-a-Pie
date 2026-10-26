"""Retire the removed `archived` document status.

Documents had a manual "archive" state that duplicated soft delete: the
original design (policy-driven tiering of superseded versions) was never
built, and what shipped behaved as a second delete. It was removed. A row
still carrying `archived` is a soft-deleted document under another name, so
it becomes `inactive`.

Two places apply this:
- startup, over the document collection, before anything reads it (the
  value no longer parses as a DocumentStatus);
- the restore door, on each row it writes, so an archive taken before the
  removal cannot bring the state back.

File reference counts need no adjustment: archiving released a document's
file references exactly as soft delete does.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection

RETIRED_STATUS = "archived"
REPLACEMENT_STATUS = "inactive"


async def retire_archived_status(collection: AsyncIOMotorCollection) -> int:
    """Rewrite every `archived` document row as `inactive`. Idempotent."""
    result = await collection.update_many(
        {"status": RETIRED_STATUS}, {"$set": {"status": REPLACEMENT_STATUS}}
    )
    return int(getattr(result, "modified_count", 0) or 0)


def retire_archived_row(row: dict[str, Any]) -> None:
    """The same rule for one row about to be restored."""
    if row.get("status") == RETIRED_STATUS:
        row["status"] = REPLACEMENT_STATUS
