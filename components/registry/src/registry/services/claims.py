"""Composite-key claim orchestration (CASE-427, two-phase since CASE-554).

Higher-level operations over the ``CompositeKeyClaim`` collection. The write
paths run a two-phase protocol — claim ``pending`` BEFORE the entry write,
fail-fast on conflict, flip ``confirmed`` after it commits — so an entry
without a claim is unrepresentable by ordering and the only inconsistency the
system can produce is a pending claim whose request died mid-flight. Those are
resolved two ways: the cheap ``reconcile_pending_claims`` startup pass (an
indexed (state, age) query — confirm if backed, delete if not), and
``resolve_stale_claim`` inline healing when a new claim collides with a stale
one (which also self-heals the rare confirmed-dangling residue of crashed
deletes, at the moment it matters).

The one-shot **backfill** (builds the domain from existing entries, stripping
losing duplicate synonyms — destructive, explicit) and the full-collection
**reconcile_orphan_claims** scan remain as ADMIN AUDIT verbs only; the scan is
off the startup path — its live-traffic race class (CASE-553) does not apply
to the pending pass, which never touches young or confirmed claims.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from pymongo import UpdateOne
from pymongo.errors import BulkWriteError, DuplicateKeyError

from ..models.composite_key_claim import CompositeKeyClaim
from ..models.entry import RegistryEntry, Synonym

logger = logging.getLogger("registry.claims")

# How long a pending claim may sit before the reconcile pass (or an inline
# collision) treats its request as dead. Requests live for seconds; a minute
# is generous without letting ghosts block a hash for long.
PENDING_GRACE_SECONDS = 60


def _entry_key_pairs(entry: RegistryEntry) -> list[tuple[str, str, str, str]]:
    """(namespace, entity_type, hash, kind) for an entry's primary key plus
    every active embedded synonym — the pairs the unified domain tracks. An
    inactive synonym (its entity is retired) holds no claim."""
    pairs = [
        (entry.namespace, entry.entity_type, entry.primary_composite_key_hash, "primary"),
    ]
    for syn in entry.active_synonyms():
        pairs.append((syn.namespace, syn.entity_type, syn.composite_key_hash, "synonym"))
    return [(ns, et, h, k) for ns, et, h, k in pairs if h]


async def resolve_stale_claim(existing: CompositeKeyClaim | None) -> bool:
    """Inline healing at collision time. True = the claim was stale and has
    been removed (caller may retry its claim once); False = leave it alone.

    Stale means: the owner entry no longer exists or no longer carries the
    claimed key (crashed delete / namespace-deletion residue / synonym
    removal), OR the claim is pending and older than the grace window with
    no backing entry (its request died before the entry write). A pending
    claim that IS backed gets confirmed here — its request died between the
    entry write and the flip — and then counts as genuine. Young pending
    claims are in-flight requests: never touched. Never steals a claim whose
    owner genuinely backs it; racing healers are arbitrated by the unique
    index on re-claim.
    """
    if existing is None:
        return False
    entry = await RegistryEntry.find_one(RegistryEntry.entry_id == existing.owner_entry_id)
    backed = entry is not None and _entry_backs_claim(entry, existing)

    if existing.state == "pending":
        # Beanie deserializes Mongo dates as naive UTC — normalize before
        # arithmetic or the subtraction raises on aware-vs-naive.
        created = existing.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        age = datetime.now(UTC) - created
        if age < timedelta(seconds=PENDING_GRACE_SECONDS):
            return False  # in-flight request — genuine for now
        if backed:
            await CompositeKeyClaim.confirm(
                existing.namespace, existing.entity_type,
                existing.composite_key_hash, existing.owner_entry_id,
            )
            return False  # healed to confirmed — genuine owner
    elif backed:
        return False  # confirmed and backed — genuine owner

    await CompositeKeyClaim.get_motor_collection().delete_one({
        "namespace": existing.namespace,
        "entity_type": existing.entity_type,
        "composite_key_hash": existing.composite_key_hash,
        "owner_entry_id": existing.owner_entry_id,
        "state": existing.state,
    })
    logger.info(
        "CASE-554: healed stale %s claim on %s (%s/%s), late owner %s",
        existing.state, existing.composite_key_hash,
        existing.namespace, existing.entity_type, existing.owner_entry_id,
    )
    return True


async def claim_entry_keys_pending(entry: RegistryEntry) -> str | None:
    """Claim-first gate for the entry create paths: claim every key pair as
    ``pending`` BEFORE the entry insert. Returns None on success or a
    conflict message — in which case any pairs already claimed for this
    entry have been rolled back and the item must NOT be inserted.

    Fail-fast is deliberate (CASE-554): a primary key colliding with an
    existing synonym is a real conflict — the composite key already resolves
    to another entity — not a log line. Claim-first also makes
    entry-without-claim unrepresentable by ordering.
    """
    claimed: list[tuple[str, str, str]] = []
    for ns, etype, key_hash, kind in _entry_key_pairs(entry):
        for attempt in (1, 2):
            try:
                await CompositeKeyClaim.claim(
                    ns, etype, key_hash, entry.entry_id, kind, state="pending"
                )
                claimed.append((ns, etype, key_hash))
                break
            except DuplicateKeyError:
                existing = await CompositeKeyClaim.find_existing(ns, etype, key_hash)
                if attempt == 1 and await resolve_stale_claim(existing):
                    continue  # healed — retry once; index arbitrates races
                owner = existing.owner_entry_id if existing else "?"
                for c_ns, c_et, c_hash in claimed:
                    await CompositeKeyClaim.release(c_ns, c_et, c_hash, entry.entry_id)
                return (
                    f"Composite key already registered under different entry: {owner}"
                )
    return None


async def confirm_entry_keys(entry: RegistryEntry) -> None:
    """Flip an inserted entry's pending claims to confirmed. Best-effort: a
    failure leaves pending-but-backed claims, which the cheap reconcile pass
    confirms on its next run — it must never fail the committed insert."""
    for ns, etype, key_hash, _kind in _entry_key_pairs(entry):
        try:
            await CompositeKeyClaim.confirm(ns, etype, key_hash, entry.entry_id)
        except Exception:
            logger.exception(
                "CASE-554: confirm failed for %s (%s/%s), entry %s — left "
                "pending for reconciliation.",
                key_hash, ns, etype, entry.entry_id,
            )


async def release_entry_keys(entry: RegistryEntry) -> None:
    """Roll back an entry's claims after its insert failed. Best-effort —
    anything left behind is pending and dies in the reconcile pass."""
    for ns, etype, key_hash, _kind in _entry_key_pairs(entry):
        try:
            await CompositeKeyClaim.release(ns, etype, key_hash, entry.entry_id)
        except Exception:
            logger.exception(
                "CASE-554: release failed for %s (%s/%s), entry %s — pending "
                "claim left for reconciliation.",
                key_hash, ns, etype, entry.entry_id,
            )


# ── Bulk forms ───────────────────────────────────────────────────────────
# The same two-phase protocol for a whole request at once: one insert for
# every pending claim, one write to confirm, one to release. A request of 500
# entries otherwise pays one database round trip per key per phase.

#: One claim: (namespace, entity_type, composite_key_hash, owner_entry_id, kind).
ClaimKey = tuple[str, str, str, str, str]

_CLAIM_CONFLICT = "Composite key already registered under different entry: {owner}"
_RELEASE_CHUNK = 500


def entry_claim_keys(entry: RegistryEntry) -> list[ClaimKey]:
    """The claims an entry's keys need: its primary key and every active
    synonym, owned by the entry."""
    return [
        (ns, etype, key_hash, entry.entry_id, kind)
        for ns, etype, key_hash, kind in _entry_key_pairs(entry)
    ]


async def _claim_one_pending(key: ClaimKey) -> str | None:
    """One pending claim, the way ``claim_entry_keys_pending`` takes each
    pair: a stale holder is healed and the claim retried once. Returns None
    when the claim is held by its owner, or the entry_id of the owner that
    blocks it ("?" when the holder cannot be read)."""
    ns, etype, key_hash, owner, kind = key
    for attempt in (1, 2):
        try:
            await CompositeKeyClaim.claim(ns, etype, key_hash, owner, kind, state="pending")
            return None
        except DuplicateKeyError:
            existing = await CompositeKeyClaim.find_existing(ns, etype, key_hash)
            if attempt == 1 and await resolve_stale_claim(existing):
                continue  # healed — retry once; the index arbitrates races
            return existing.owner_entry_id if existing else "?"
    return "?"


async def _claim_group_one_by_one(group: Sequence[ClaimKey]) -> str | None:
    """Claim a group pair by pair, releasing what it took if one is blocked."""
    taken: list[ClaimKey] = []
    for key in group:
        if not key[2]:
            continue
        blocker = await _claim_one_pending(key)
        if blocker is not None:
            await release_keys_bulk(taken)
            return blocker
        taken.append(key)
    return None


async def claim_keys_pending_bulk(groups: Sequence[Sequence[ClaimKey]]) -> list[str | None]:
    """Claim many groups of keys as ``pending`` in one insert.

    Each group is all-or-nothing, like one call of
    ``claim_entry_keys_pending``: either every key in it is held by its owner
    afterwards, or none of the keys it took in this call are. The result
    names, per group and in input order, None (claimed) or the entry_id of
    the owner that blocks it.

    The insert is unordered, so every key that is free is claimed in the one
    round trip. Only a key that hits the unique index goes the per-key way:
    a key its own owner already holds is fine, a stale holder is healed and
    the key retried once, a genuine owner blocks the group. Blocked groups
    are then released. A group blocked only by another group of the same
    batch that was itself rolled back is claimed again, pair by pair: taken
    one at a time, as before, it would have found the key free.
    """
    collection = CompositeKeyClaim.get_motor_collection()
    now = datetime.now(UTC)
    docs: list[dict] = []
    doc_group: list[int] = []
    for gi, group in enumerate(groups):
        for ns, etype, key_hash, owner, kind in group:
            if not key_hash:
                continue
            docs.append({
                "namespace": ns,
                "entity_type": etype,
                "composite_key_hash": key_hash,
                "owner_entry_id": owner,
                "kind": kind,
                "state": "pending",
                "created_at": now,
            })
            doc_group.append(gi)

    duplicates: list[int] = []
    if docs:
        try:
            await collection.insert_many(docs, ordered=False)
        except BulkWriteError as e:
            for error in e.details.get("writeErrors", []):
                if error.get("code") != 11000:
                    raise
                duplicates.append(error["index"])

    blocked: list[str | None] = [None] * len(groups)
    for di in sorted(duplicates):
        gi = doc_group[di]
        if blocked[gi] is not None:
            continue
        d = docs[di]
        blocked[gi] = await _claim_one_pending((
            d["namespace"], d["entity_type"], d["composite_key_hash"],
            d["owner_entry_id"], d["kind"],
        ))

    failed = [gi for gi, b in enumerate(blocked) if b is not None]
    if not failed:
        return blocked
    await release_keys_bulk([key for gi in failed for key in groups[gi]])

    rolled_back = {key[3] for gi in failed for key in groups[gi]}
    for gi in failed:
        own = {key[3] for key in groups[gi]}
        if blocked[gi] in rolled_back - own:
            blocked[gi] = await _claim_group_one_by_one(groups[gi])
    return blocked


async def confirm_keys_bulk(keys: Sequence[ClaimKey]) -> None:
    """Flip pending claims to confirmed after their entries were written, in
    one write. Each update names the exact claim (namespace, entity type,
    hash and owner), never every pending claim of an owner: that would also
    confirm a claim another request holds on the same entry while it is
    still in flight. Best-effort, as ``confirm_entry_keys``: a failure leaves
    pending-but-backed claims for the reconcile pass."""
    ops = [
        UpdateOne(
            {
                "namespace": ns,
                "entity_type": etype,
                "composite_key_hash": key_hash,
                "owner_entry_id": owner,
            },
            {"$set": {"state": "confirmed"}},
        )
        for ns, etype, key_hash, owner, _kind in keys
        if key_hash
    ]
    if not ops:
        return
    try:
        await CompositeKeyClaim.get_motor_collection().bulk_write(ops, ordered=False)
    except Exception:
        logger.exception(
            "Bulk confirm of %d claim(s) failed — left pending for reconciliation.",
            len(ops),
        )


async def release_keys_bulk(keys: Sequence[ClaimKey]) -> None:
    """Delete claims, each only if its named owner holds it, in a few
    writes. Best-effort, as ``release_entry_keys``: anything left behind is
    pending and dies in the reconcile pass."""
    clauses = [
        {
            "namespace": ns,
            "entity_type": etype,
            "composite_key_hash": key_hash,
            "owner_entry_id": owner,
        }
        for ns, etype, key_hash, owner, _kind in keys
        if key_hash
    ]
    collection = CompositeKeyClaim.get_motor_collection()
    for start in range(0, len(clauses), _RELEASE_CHUNK):
        chunk = clauses[start:start + _RELEASE_CHUNK]
        try:
            await collection.delete_many({"$or": chunk})
        except Exception:
            logger.exception(
                "Bulk release of %d claim(s) failed — left for reconciliation.",
                len(chunk),
            )


async def claim_entries_keys_pending(entries: Sequence[RegistryEntry]) -> list[str | None]:
    """``claim_entry_keys_pending`` for many entries in one insert: per entry,
    in input order, None or the conflict message."""
    blocked = await claim_keys_pending_bulk([entry_claim_keys(e) for e in entries])
    return [None if owner is None else _CLAIM_CONFLICT.format(owner=owner) for owner in blocked]


async def confirm_entries_keys(entries: Sequence[RegistryEntry]) -> None:
    """``confirm_entry_keys`` for many entries in one write."""
    await confirm_keys_bulk([key for e in entries for key in entry_claim_keys(e)])


async def release_entries_keys(entries: Sequence[RegistryEntry]) -> None:
    """``release_entry_keys`` for many entries in a few writes."""
    await release_keys_bulk([key for e in entries for key in entry_claim_keys(e)])


def releasable_synonyms(entry: RegistryEntry) -> list[Synonym]:
    """The synonyms retiring this entry releases: every active synonym except
    the auto-synonym (the entity's own address). The primary key is not a
    synonym and always stays claimed — that is what lets a re-post of the same
    identity revive the retired entity instead of forking it."""
    return [s for s in entry.active_synonyms() if not entry.is_auto_synonym(s)]


async def retire_entry_synonyms(
    entry: RegistryEntry, updated_by: str | None = None, *, dry_run: bool = False,
) -> list[Synonym]:
    """Retire an entry's synonyms: mark the releasable ones inactive and drop
    their claims, so they neither block another entity from taking the key nor
    resolve. They stay recorded on the entry for revival. Returns the synonyms
    released (would be released, on a dry run). Idempotent.

    The entry is written before the claims go: a crash in between leaves a
    claim its owner no longer backs, which inline healing frees at the next
    collision — never a released claim on a synonym still marked active.
    """
    released = releasable_synonyms(entry)
    if dry_run or not released:
        return released
    for syn in released:
        syn.status = "inactive"
    entry.rebuild_search_values()
    entry.updated_at = datetime.now(UTC)
    entry.updated_by = updated_by
    await entry.save()
    for syn in released:
        try:
            await CompositeKeyClaim.release(
                syn.namespace, syn.entity_type, syn.composite_key_hash,
                owner_entry_id=entry.entry_id,
            )
        except Exception:
            logger.exception(
                "Release failed for retired synonym %s (%s/%s) of entry %s — "
                "the claim is unbacked and heals at the next collision.",
                syn.composite_key_hash, syn.namespace, syn.entity_type, entry.entry_id,
            )
    return released


async def revive_entry_synonyms(
    entry: RegistryEntry, updated_by: str | None = None,
) -> tuple[list[Synonym], list[tuple[Synonym, str]]]:
    """Revive an entry's inactive synonyms: re-claim each one whose key is
    free. One another entity took while this one was retired stays inactive —
    the entity is revived without it. Returns (reclaimed, [(synonym, holder
    entry_id), …] for those left inactive). Idempotent.

    Same two-phase order as a synonym add: claim pending, write the entry,
    confirm.
    """
    reclaimed: list[Synonym] = []
    taken: list[tuple[Synonym, str]] = []
    for syn in entry.synonyms:
        if syn.status != "inactive":
            continue
        for attempt in (1, 2):
            try:
                await CompositeKeyClaim.claim(
                    syn.namespace, syn.entity_type, syn.composite_key_hash,
                    entry.entry_id, "synonym", state="pending",
                )
                reclaimed.append(syn)
                break
            except DuplicateKeyError:
                existing = await CompositeKeyClaim.find_existing(
                    syn.namespace, syn.entity_type, syn.composite_key_hash,
                )
                if attempt == 1 and await resolve_stale_claim(existing):
                    continue  # healed — retry once; index arbitrates races
                taken.append((syn, existing.owner_entry_id if existing else "?"))
                break
    if not reclaimed:
        return reclaimed, taken

    for syn in reclaimed:
        syn.status = "active"
    entry.rebuild_search_values()
    entry.updated_at = datetime.now(UTC)
    entry.updated_by = updated_by
    try:
        await entry.save()
    except Exception:
        for syn in reclaimed:
            await CompositeKeyClaim.release(
                syn.namespace, syn.entity_type, syn.composite_key_hash,
                owner_entry_id=entry.entry_id,
            )
        raise
    for syn in reclaimed:
        await CompositeKeyClaim.confirm(
            syn.namespace, syn.entity_type, syn.composite_key_hash, entry.entry_id,
        )
    return reclaimed, taken


async def reconcile_pending_claims(
    grace_seconds: int = PENDING_GRACE_SECONDS,
) -> dict[str, int]:
    """The cheap startup reconcile: resolve pending claims older than the
    grace window. Backed by an entry → confirm (the request died between
    insert and flip); unbacked → delete (it died before the insert).

    O(pending) via the partial (state, created_at) index — confirmed claims,
    the overwhelming majority, are never read. Young pending claims belong
    to in-flight requests and are never touched, which is what makes this
    safe to run concurrently with live traffic (the CASE-553 failure mode of
    the full-scan reconcile).
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=grace_seconds)
    claims_coll = CompositeKeyClaim.get_motor_collection()
    summary = {"checked": 0, "confirmed": 0, "deleted": 0}

    async for doc in claims_coll.find(
        {"state": "pending", "created_at": {"$lt": cutoff}}
    ):
        summary["checked"] += 1
        entry = await RegistryEntry.find_one(
            RegistryEntry.entry_id == doc["owner_entry_id"]
        )
        backed = entry is not None and _entry_doc_backs_claim(
            entry.model_dump(), doc
        )
        if backed:
            await claims_coll.update_one(
                {"_id": doc["_id"], "state": "pending"},
                {"$set": {"state": "confirmed"}},
            )
            summary["confirmed"] += 1
        else:
            await claims_coll.delete_one({"_id": doc["_id"], "state": "pending"})
            summary["deleted"] += 1

    if summary["checked"]:
        logger.info("CASE-554 pending reconcile: %s", summary)
    return summary


async def backfill_claims() -> dict[str, int]:
    """Build the CompositeKeyClaim domain from existing RegistryEntry data.

    Two passes over all entries (ordered by created_at for deterministic
    tie-breaks): primary claims first, then synonym claims. Because the unique
    index is the duplicate *detector*, the first claimant of a
    (namespace, entity_type, hash) wins and primaries — claimed first — beat
    synonyms.

    Losers:
      - A losing SYNONYM (its hash already claimed elsewhere) is a true
        duplicate: strip it from its entry's embedded array + log (audited
        destructive converge, CASE-427).
      - A losing PRIMARY (two entries with the same primary key in one
        namespace/entity_type — only possible for data predating the primary
        unique index) is logged as a hard warning; entries are NEVER
        auto-deleted.

    Idempotent: re-running finds everything already claimed by the same owner
    (model.claim self-retry no-ops) and there are no remaining loser synonyms
    to strip.
    """
    summary = {
        "primary_claimed": 0,
        "synonym_claimed": 0,
        "synonym_losers_stripped": 0,
        "primary_collisions_logged": 0,
    }

    # Pass 1 — primary claims (winners of any cross-source tie).
    async for entry in RegistryEntry.find_all().sort("+created_at"):
        key_hash = entry.primary_composite_key_hash
        if not key_hash:
            continue
        try:
            await CompositeKeyClaim.claim(
                entry.namespace, entry.entity_type, key_hash, entry.entry_id, "primary"
            )
            summary["primary_claimed"] += 1
        except DuplicateKeyError:
            existing = await CompositeKeyClaim.find_existing(
                entry.namespace, entry.entity_type, key_hash
            )
            owner = existing.owner_entry_id if existing else "?"
            logger.warning(
                "CASE-427 backfill: PRIMARY-key collision — entry %s primary "
                "hash %s in %s/%s already claimed by %s. Not auto-resolving; "
                "manual review needed.",
                entry.entry_id, key_hash, entry.namespace, entry.entity_type, owner,
            )
            summary["primary_collisions_logged"] += 1

    # Pass 2 — synonym claims; losers get stripped from their entry.
    async for entry in RegistryEntry.find_all().sort("+created_at"):
        if not entry.synonyms:
            continue
        survivors = []
        changed = False
        for syn in entry.synonyms:
            if not syn.composite_key_hash or syn.status == "inactive":
                survivors.append(syn)
                continue
            try:
                await CompositeKeyClaim.claim(
                    syn.namespace, syn.entity_type, syn.composite_key_hash,
                    entry.entry_id, "synonym",
                )
                summary["synonym_claimed"] += 1
                survivors.append(syn)
            except DuplicateKeyError:
                existing = await CompositeKeyClaim.find_existing(
                    syn.namespace, syn.entity_type, syn.composite_key_hash
                )
                winner = existing.owner_entry_id if existing else "?"
                logger.warning(
                    "CASE-427 backfill: stripping duplicate synonym from entry "
                    "%s — hash %s (%s/%s) is owned by %s. Removed synonym "
                    "composite_key=%s.",
                    entry.entry_id, syn.composite_key_hash, syn.namespace,
                    syn.entity_type, winner, syn.composite_key,
                )
                summary["synonym_losers_stripped"] += 1
                changed = True  # drop this synonym (not appended to survivors)
        if changed:
            entry.synonyms = survivors
            entry.rebuild_search_values()
            await entry.save()

    logger.info("CASE-427 backfill complete: %s", summary)
    return summary


async def reconcile_orphan_claims() -> dict[str, int]:
    """Full-collection audit: delete claims whose backing entry/synonym no
    longer exists. ADMIN VERB — not on the startup path since the two-phase
    protocol (CASE-554); day-to-day consistency is kept by
    reconcile_pending_claims + inline healing, and this scan's concurrency
    hazard against live traffic (CASE-553) is why it stays manual.

    Safe + idempotent — only deletes dangling claims, never mutates entries.
    Catches confirmed-dangling residue in bulk: claims for deleted entries,
    cross-namespace-synonym residue after namespace deletion, stale claims
    from entry mutations.

    Uses batched queries (500 owner IDs per round) instead of per-claim
    lookups to avoid the N+1 pattern that takes 20+ minutes on slow storage.
    """
    BATCH_SIZE = 500
    claims_coll = CompositeKeyClaim.get_motor_collection()
    entries_coll = RegistryEntry.get_motor_collection()
    summary = {"checked": 0, "orphans_deleted": 0}

    batch: list = []
    async for claim_doc in claims_coll.find():
        batch.append(claim_doc)
        if len(batch) < BATCH_SIZE:
            continue

        deleted = await _reconcile_batch(batch, entries_coll, claims_coll)
        summary["checked"] += len(batch)
        summary["orphans_deleted"] += deleted
        batch = []

    if batch:
        deleted = await _reconcile_batch(batch, entries_coll, claims_coll)
        summary["checked"] += len(batch)
        summary["orphans_deleted"] += deleted

    if summary["orphans_deleted"]:
        logger.info("CASE-427 reconcile: %s", summary)
    return summary


async def _reconcile_batch(batch, entries_coll, claims_coll) -> int:
    """Reconcile one batch of claim docs. Returns count of orphans deleted."""
    owner_ids = list({doc["owner_entry_id"] for doc in batch})
    existing = {}
    async for entry in entries_coll.find(
        {"entry_id": {"$in": owner_ids}},
        {"entry_id": 1, "namespace": 1, "entity_type": 1,
         "primary_composite_key_hash": 1, "synonyms": 1},
    ):
        existing[entry["entry_id"]] = entry

    orphan_ids = []
    for doc in batch:
        entry = existing.get(doc["owner_entry_id"])
        if entry is None or not _entry_doc_backs_claim(entry, doc):
            orphan_ids.append(doc["_id"])

    if orphan_ids:
        await claims_coll.delete_many({"_id": {"$in": orphan_ids}})
    return len(orphan_ids)


def _entry_doc_backs_claim(entry: dict, claim_doc: dict) -> bool:
    """Like _entry_backs_claim but operates on raw dicts (no ODM overhead)."""
    if (
        entry.get("namespace") == claim_doc["namespace"]
        and entry.get("entity_type") == claim_doc["entity_type"]
        and entry.get("primary_composite_key_hash") == claim_doc["composite_key_hash"]
    ):
        return True
    return any(
        s.get("namespace") == claim_doc["namespace"]
        and s.get("entity_type") == claim_doc["entity_type"]
        and s.get("composite_key_hash") == claim_doc["composite_key_hash"]
        and s.get("status") != "inactive"
        for s in entry.get("synonyms", [])
    )


def _entry_backs_claim(entry: RegistryEntry, claim: CompositeKeyClaim) -> bool:
    """True if the entry still owns the claimed (namespace, entity_type, hash)
    as either its primary key or one of its active embedded synonyms."""
    if (
        entry.namespace == claim.namespace
        and entry.entity_type == claim.entity_type
        and entry.primary_composite_key_hash == claim.composite_key_hash
    ):
        return True
    return any(
        s.namespace == claim.namespace
        and s.entity_type == claim.entity_type
        and s.composite_key_hash == claim.composite_key_hash
        for s in entry.active_synonyms()
    )


async def _delete_claim(claim: CompositeKeyClaim) -> None:
    await CompositeKeyClaim.get_motor_collection().delete_one(
        {
            "namespace": claim.namespace,
            "entity_type": claim.entity_type,
            "composite_key_hash": claim.composite_key_hash,
        }
    )
