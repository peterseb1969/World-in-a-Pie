"""Writes to a Registry entry that cannot overwrite each other.

An entry is often changed by reading it, changing it in memory and writing it
back. Two such writers that read the same entry each write their own copy, and
the later write silently drops the earlier one's change: a synonym added by one
caller vanishes, a soft delete is undone. Here every write increments the
entry's ``revision`` and a read-modify-write only lands if the entry still has
the revision it read. A writer that lost re-reads the entry and applies its
change again, so the change is decided against the entry as it now is.

Writes set only the fields they changed. A writer that changes an entry
without reading it first (a guarded status flip) does not need the revision
filter, but must still increment it — use ``REVISION_INC``.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable, Iterable
from typing import Any

from beanie.odm.utils.encoder import Encoder

from ..models.entry import RegistryEntry

logger = logging.getLogger(__name__)

#: Attempts a read-modify-write makes before it reports a conflict. Each lost
#: attempt means another writer landed in between, so N writers on one entry
#: can cost a writer up to N-1 attempts; running out takes sustained
#: contention well beyond that.
MAX_ATTEMPTS = 10

#: Upper bound of the random pause before retry n is n times this (seconds).
#: Writers that lost together would otherwise retry in lockstep and collide
#: again.
RETRY_JITTER = 0.005

#: Merge into an update document so a write that does not read first still
#: tells guarded writers that the entry changed.
REVISION_INC = {"revision": 1}

#: A mutate function returns this when the entry needs no write.
UNCHANGED = object()


class EntryWriteConflict(Exception):
    """The entry kept changing under the writer for every attempt."""

    error_code = "concurrency_conflict"


def revision_filter(entry_id: str, revision: int) -> dict[str, Any]:
    """Match the entry only while it still has ``revision``. An entry written
    before the counter existed has no field, which counts as 0."""
    if revision == 0:
        return {"entry_id": entry_id, "revision": {"$in": [0, None]}}
    return {"entry_id": entry_id, "revision": revision}


def set_fields(entry: RegistryEntry, fields: Iterable[str]) -> dict[str, Any]:
    """An update that writes ``fields`` from ``entry`` and increments the revision."""
    encoded = Encoder().encode(entry)
    return {"$set": {f: encoded[f] for f in fields}, "$inc": dict(REVISION_INC)}


async def write_entry(
    entry: RegistryEntry, fields: Iterable[str], extra_filter: dict[str, Any] | None = None,
) -> bool:
    """Write ``fields`` of ``entry`` if the stored entry still has the revision
    ``entry`` was read with. True when it landed (``entry.revision`` is then
    the stored one), False when another writer got there first."""
    flt = revision_filter(entry.entry_id, entry.revision)
    if extra_filter:
        flt.update(extra_filter)
    result = await RegistryEntry.get_motor_collection().update_one(flt, set_fields(entry, fields))
    if result.matched_count != 1:
        return False
    entry.revision += 1
    return True


async def apply_to_entry(
    entry_id: str,
    mutate: Callable[[RegistryEntry], Any],
    fields: Iterable[str],
    *,
    first: RegistryEntry | None = None,
    read_filter: dict[str, Any] | None = None,
) -> tuple[RegistryEntry | None, Any]:
    """Read the entry, change it with ``mutate`` and write ``fields`` back,
    starting over from a fresh read whenever another writer changed the entry
    in between.

    ``mutate`` changes the entry in place and returns a result for the caller,
    or ``UNCHANGED`` when there is nothing to write. It must decide from the
    entry it is given — it runs again on every attempt. ``first`` is an entry
    the caller already read, used for the first attempt. ``read_filter`` is
    merged into every read (e.g. ``{"status": "active"}``); when no entry
    matches, returns ``(None, None)``.

    Returns ``(entry, result)``. Raises ``EntryWriteConflict`` when every
    attempt lost.
    """
    fields = list(fields)
    entry = first
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if entry is None:
            entry = await RegistryEntry.find_one({"entry_id": entry_id, **(read_filter or {})})
            if entry is None:
                return None, None
        result = mutate(entry)
        if result is UNCHANGED:
            return entry, result
        if await write_entry(entry, fields):
            return entry, result
        logger.info("Entry %s changed under a write (attempt %d); re-reading", entry_id, attempt)
        await asyncio.sleep(random.uniform(0, RETRY_JITTER * attempt))  # nosec B311  # retry jitter, not a secret
        entry = None
    raise EntryWriteConflict(
        f"Entry {entry_id} kept changing while it was being written ({MAX_ATTEMPTS} attempts); "
        "retry the request"
    )
