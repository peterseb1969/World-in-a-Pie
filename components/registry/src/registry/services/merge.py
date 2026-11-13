"""Merging one Registry entry into another.

Merging D into P declares them one entity, with P's id the canonical one.
Afterwards:

- D is inactive and records ``merged_into = P``; resolving D's id follows it
  (through further merges), so references already stored as D keep resolving.
- Every key D held now belongs to P: D's id as the key ``{"entry_id": D}``,
  D's primary key and D's synonyms, each with its claim re-pointed to P. D
  keeps a record of them, all inactive, and its primary key hash is cleared,
  so re-registering D's identity answers P.
- The keys a merge gives P in D's place (D's id and primary key) carry
  ``merged_from = D``, and retiring P keeps them claimed, as it keeps P's own.

A merge takes only keys D or P hold. A key some third entry genuinely holds is
a conflict, reported with its holder, and nothing is merged.

Order: the claims move first (pending, so no healer takes them while P is
being written), then P is written, the claims are confirmed, and D is retired
last. A failure before D's write puts the claims back where it can; a failure
at D's write leaves P holding D's keys while D is still active — a lookup of
one of those keys then answers ambiguous, never with nothing — and retrying
the merge completes it. A retry of a merge that completed answers merged
again.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pymongo.errors import DuplicateKeyError

from ..models.composite_key_claim import CompositeKeyClaim
from ..models.entry import RegistryEntry, Synonym
from .claims import confirm_keys_bulk, new_claim_token, release_keys_bulk, resolve_stale_claim
from .entry_actor import STAMP_FIELDS, EntryActor
from .entry_writes import UNCHANGED, apply_to_entry
from .hash import HashService

#: How many merges resolving an id follows before giving up. Merges only ever
#: point at an entry that was active when merged, so a chain cannot loop; the
#: bound only caps the reads.
MERGE_CHAIN_LIMIT = 10


class MergeKeyConflict(Exception):
    """A key the merge would move is held by an entry other than the two."""

    error_code = "already_exists"

    def __init__(self, synonym: Synonym, holder: str):
        super().__init__(
            f"Key {synonym.composite_key} is held by entry {holder}, not by either "
            "entry being merged; nothing was merged"
        )
        self.details = {
            "rule": "key_held_elsewhere", "holder": holder,
            "composite_key": synonym.composite_key,
        }


class MergedEntryChanged(Exception):
    """The deprecated entry gained a key while it was being merged."""

    error_code = "concurrency_conflict"


def moving_keys(deprecated: RegistryEntry, actor: EntryActor) -> list[Synonym]:
    """The synonyms P gains from D: D's id, D's primary key, and D's synonyms
    as they are (an inactive one moves as a record only)."""
    def own(key: dict, key_hash: str) -> Synonym:
        return Synonym(
            namespace=deprecated.namespace, entity_type=deprecated.entity_type,
            composite_key=key, composite_key_hash=key_hash,
            created_by=actor.by, merged_from=deprecated.entry_id,
        )

    id_key = {"entry_id": deprecated.entry_id}
    keys = [own(id_key, HashService.compute_composite_key_hash(id_key))]
    if deprecated.primary_composite_key_hash:
        keys.append(own(deprecated.primary_composite_key, deprecated.primary_composite_key_hash))
    keys.extend(s.model_copy() for s in deprecated.synonyms)
    return keys


def _scope(syn: Synonym) -> tuple[str, str, str]:
    return syn.namespace, syn.entity_type, syn.composite_key_hash


async def _check_holders(claimable: list[Synonym], allowed: tuple[str, str]) -> None:
    """Refuse the merge before any write if a third entry holds one of the keys.
    A stale holder (one that no longer backs its claim) is healed."""
    for syn in claimable:
        existing = await CompositeKeyClaim.find_existing(*_scope(syn))
        if existing is None or existing.owner_entry_id in allowed:
            continue
        if not await resolve_stale_claim(existing):
            raise MergeKeyConflict(syn, existing.owner_entry_id)


class _ClaimMove:
    """The claims one merge moved or took, so a failed merge can put them back."""

    def __init__(self, preferred_id: str, deprecated_id: str):
        self.preferred_id = preferred_id
        self.allowed = (deprecated_id, preferred_id)
        self.token = new_claim_token()
        self.moved: list[dict] = []
        self.taken: list[Synonym] = []

    async def take(self, syn: Synonym) -> None:
        before = await CompositeKeyClaim.transfer(*_scope(syn), self.preferred_id, self.allowed)
        if before is not None:
            self.moved.append(before)
            return
        try:
            await CompositeKeyClaim.claim(
                *_scope(syn), self.preferred_id, "synonym", state="pending", token=self.token,
            )
        except DuplicateKeyError:
            existing = await CompositeKeyClaim.find_existing(*_scope(syn))
            raise MergeKeyConflict(syn, existing.owner_entry_id if existing else "?") from None
        self.taken.append(syn)

    async def undo(self) -> None:
        for before in self.moved:
            await CompositeKeyClaim.restore(before, self.preferred_id)
        await release_keys_bulk(
            [(*_scope(s), self.preferred_id, "synonym") for s in self.taken], self.token,
        )

    async def confirm(self, claimable: list[Synonym]) -> None:
        await confirm_keys_bulk([(*_scope(s), self.preferred_id, "synonym") for s in claimable])


def _absorb(keys: list[Synonym], now: datetime, actor: EntryActor):
    def mutate(entry: RegistryEntry):
        present = {_scope(s) for s in entry.synonyms}
        new = [s for s in keys if _scope(s) not in present]
        if not new:
            return UNCHANGED
        entry.synonyms.extend(new)
        entry.rebuild_search_values()
        actor.stamp(entry, now)
        return None
    return mutate


def _retire(
    preferred_id: str, moved: set[tuple[str, str, str]], now: datetime, actor: EntryActor,
):
    def mutate(entry: RegistryEntry):
        gained = [s for s in entry.active_synonyms() if _scope(s) not in moved]
        if gained:
            raise MergedEntryChanged(
                f"Entry {entry.entry_id} gained a key while it was being merged; "
                "retry the merge"
            )
        for syn in entry.synonyms:
            syn.status = "inactive"
        entry.status = "inactive"
        entry.merged_into = preferred_id
        entry.primary_composite_key_hash = ""
        entry.rebuild_search_values()
        actor.stamp(entry, now)
        return None
    return mutate


async def merge_into(
    preferred: RegistryEntry, deprecated: RegistryEntry, actor: EntryActor,
) -> None:
    """Merge ``deprecated`` into ``preferred`` (both active, same namespace
    and entity type, permission already checked). Raises MergeKeyConflict
    (nothing merged), MergedEntryChanged or EntryWriteConflict."""
    keys = moving_keys(deprecated, actor)
    claimable = [s for s in keys if s.status != "inactive"]
    move = _ClaimMove(preferred.entry_id, deprecated.entry_id)
    await _check_holders(claimable, move.allowed)

    now = datetime.now(UTC)
    try:
        for syn in claimable:
            await move.take(syn)
        await apply_to_entry(
            preferred.entry_id, _absorb(keys, now, actor),
            ("synonyms", "search_values", *STAMP_FIELDS), first=preferred,
        )
    except BaseException:
        await move.undo()
        raise
    await move.confirm(claimable)

    retired, _ = await apply_to_entry(
        deprecated.entry_id, _retire(preferred.entry_id, {_scope(s) for s in keys}, now, actor),
        ("status", "merged_into", "primary_composite_key_hash", "synonyms",
         "search_values", *STAMP_FIELDS),
        first=deprecated, read_filter={"status": "active"},
    )
    if retired is None:
        raise MergedEntryChanged(
            f"Entry {deprecated.entry_id} was retired or merged by another request "
            "while it was being merged"
        )


async def follow_merges(entry: RegistryEntry | None) -> RegistryEntry | None:
    """The entry ``entry`` was (through any chain of merges) merged into, or
    ``entry`` itself when it was not merged. None when the chain is broken or
    longer than MERGE_CHAIN_LIMIT."""
    for _ in range(MERGE_CHAIN_LIMIT):
        if entry is None or not entry.merged_into:
            return entry
        entry = await RegistryEntry.find_one({"entry_id": entry.merged_into})
    return None
