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
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

from pymongo import UpdateOne
from pymongo.errors import BulkWriteError, DuplicateKeyError

from ..models.composite_key_claim import CompositeKeyClaim
from ..models.entry import RegistryEntry, Synonym
from .entry_actor import STAMP_FIELDS, EntryActor
from .entry_writes import UNCHANGED, apply_to_entry

logger = logging.getLogger("registry.claims")

# How long a pending claim may sit before the reconcile pass (or an inline
# collision) treats its request as dead. Requests live for seconds; a minute
# is generous without letting ghosts block a hash for long.
PENDING_GRACE_SECONDS = 60


def new_claim_token() -> str:
    """A token naming one request's pending claims. The request passes it to
    the claim call and to its rollback, which then deletes only the claims
    that request inserted and nobody has taken over since."""
    return uuid.uuid4().hex


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
        # A request that takes the claim over meanwhile re-dates it; this
        # delete then misses, which is right — it is in flight again.
        "created_at": existing.created_at,
    })
    logger.info(
        "CASE-554: healed stale %s claim on %s (%s/%s), late owner %s",
        existing.state, existing.composite_key_hash,
        existing.namespace, existing.entity_type, existing.owner_entry_id,
    )
    return True


async def claim_entry_keys_pending(entry: RegistryEntry, token: str) -> str | None:
    """Claim-first gate for one entry: claim every key pair as ``pending``
    BEFORE the entry insert. Returns None on success or a conflict message —
    in which case the pairs this call took have been rolled back and the
    entry must NOT be inserted. The single-entry form of
    ``claim_entries_keys_pending``. ``token`` (``new_claim_token()``) is
    required: it is what the caller hands release_entry_keys to roll back
    only its own claims, and a token minted here could not be.

    Fail-fast is deliberate (CASE-554): a primary key colliding with an
    existing synonym is a real conflict — the composite key already resolves
    to another entity — not a log line. Claim-first also makes
    entry-without-claim unrepresentable by ordering.
    """
    return (await claim_entries_keys_pending([entry], token))[0]


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


async def release_entry_keys(entry: RegistryEntry, token: str) -> None:
    """Roll back an entry's claims after its insert failed: with the claiming
    request's ``token``, only the claims that request inserted. Best-effort —
    anything left behind is pending and dies in the reconcile pass."""
    await release_entries_keys([entry], token)


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


async def _claim_one_pending(key: ClaimKey, token: str) -> str | None:
    """One pending claim, taken under the request's token: a stale holder is
    healed and the claim retried once. Returns None when the claim is held by
    its owner, or the entry_id of the owner that blocks it ("?" when the
    holder cannot be read). A claim the owner already held is taken over
    without the token, so the request's rollback leaves it."""
    ns, etype, key_hash, owner, kind = key
    for attempt in (1, 2):
        try:
            await CompositeKeyClaim.claim(
                ns, etype, key_hash, owner, kind, state="pending", token=token,
            )
            return None
        except DuplicateKeyError:
            existing = await CompositeKeyClaim.find_existing(ns, etype, key_hash)
            if attempt == 1 and await resolve_stale_claim(existing):
                continue  # healed — retry once; the index arbitrates races
            return existing.owner_entry_id if existing else "?"
    return "?"


async def _claim_group_one_by_one(group: Sequence[ClaimKey], token: str) -> str | None:
    """Claim a group pair by pair, releasing what it took if one is blocked."""
    taken: list[ClaimKey] = []
    for key in group:
        if not key[2]:
            continue
        blocker = await _claim_one_pending(key, token)
        if blocker is not None:
            await release_keys_bulk(taken, token)
            return blocker
        taken.append(key)
    return None


async def claim_keys_pending_bulk(
    groups: Sequence[Sequence[ClaimKey]], token: str | None = None,
) -> list[str | None]:
    """Claim many groups of keys as ``pending`` in one insert.

    Each group is all-or-nothing, like one call of
    ``claim_entry_keys_pending``: either every key in it is held by its owner
    afterwards, or none of the keys it took in this call are. A key its owner
    already held before the call is not one the call took: a blocked group
    leaves it held. The result names, per group and in input order, None
    (claimed) or the entry_id of the owner that blocks it.

    The claims this call inserts carry ``token`` (a fresh one when omitted).
    A caller that may roll back after the call — its entry write failed —
    passes its own token here and to ``release_keys_bulk``, which then
    deletes only what this call inserted.

    The insert is unordered, so every key that is free is claimed in the one
    round trip. Only a key that hits the unique index goes the per-key way:
    a key its own owner already holds is fine, a stale holder is healed and
    the key retried once, a genuine owner blocks the group. Blocked groups
    are then released. A group blocked only by another group of the same
    batch that was itself rolled back is claimed again, pair by pair: taken
    one at a time, as before, it would have found the key free.
    """
    token = token or new_claim_token()
    docs, doc_group = _pending_claim_docs(groups, token)
    duplicates = await _insert_claims(docs)
    blocked: list[str | None] = [None] * len(groups)
    for di in sorted(duplicates):
        gi = doc_group[di]
        if blocked[gi] is None:
            blocked[gi] = await _claim_one_pending(_doc_key(docs[di]), token)

    failed = [gi for gi, b in enumerate(blocked) if b is not None]
    if failed:
        await release_keys_bulk([key for gi in failed for key in groups[gi]], token)
        await _reclaim_blocked_by_rollback(groups, blocked, failed, token)
    return blocked


def _pending_claim_docs(
    groups: Sequence[Sequence[ClaimKey]], token: str,
) -> tuple[list[dict], list[int]]:
    """The pending-claim documents of every key with a hash, and for each
    the index of the group it belongs to."""
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
                "claim_token": token,
            })
            doc_group.append(gi)
    return docs, doc_group


async def _insert_claims(docs: list[dict]) -> list[int]:
    """Insert the claims unordered; the indexes of those that hit the unique
    index. Any other write error propagates."""
    if not docs:
        return []
    duplicates: list[int] = []
    try:
        await CompositeKeyClaim.get_motor_collection().insert_many(docs, ordered=False)
    except BulkWriteError as e:
        for error in e.details.get("writeErrors", []):
            if error.get("code") != 11000:
                raise
            duplicates.append(error["index"])
    return duplicates


def _doc_key(doc: dict) -> ClaimKey:
    return (
        doc["namespace"], doc["entity_type"], doc["composite_key_hash"],
        doc["owner_entry_id"], doc["kind"],
    )


async def _reclaim_blocked_by_rollback(
    groups: Sequence[Sequence[ClaimKey]], blocked: list[str | None],
    failed: list[int], token: str,
) -> None:
    """Claim again, pair by pair, each released group whose only blocker was
    another group of the batch that was itself rolled back: taken one at a
    time, it would have found the key free. Updates ``blocked`` in place."""
    rolled_back = {key[3] for gi in failed for key in groups[gi]}
    for gi in failed:
        own = {key[3] for key in groups[gi]}
        if blocked[gi] in rolled_back - own:
            blocked[gi] = await _claim_group_one_by_one(groups[gi], token)


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
            {"$set": {"state": "confirmed", "claim_token": None}},
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


async def release_keys_bulk(keys: Sequence[ClaimKey], token: str | None = None) -> None:
    """Delete claims, each only if its named owner holds it, in a few
    writes. With a ``token`` — a request rolling back its own claims — only
    the claims that request inserted are deleted: never one its entry held
    before, nor one another request has taken over since. Without one — a
    synonym removed, an entity retired — the owner's claim goes, whoever
    inserted it. Best-effort: anything left behind is pending and dies in the
    reconcile pass."""
    scope = {} if token is None else {"claim_token": token}
    clauses = [
        {
            "namespace": ns,
            "entity_type": etype,
            "composite_key_hash": key_hash,
            "owner_entry_id": owner,
            **scope,
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


async def claim_entries_keys_pending(
    entries: Sequence[RegistryEntry], token: str | None = None,
) -> list[str | None]:
    """Claim every entry's keys pending in one insert: per entry, in input
    order, None or the conflict message. Pass the request's token to roll
    back later with ``release_entries_keys``."""
    blocked = await claim_keys_pending_bulk([entry_claim_keys(e) for e in entries], token)
    return [None if owner is None else _CLAIM_CONFLICT.format(owner=owner) for owner in blocked]


async def confirm_entries_keys(entries: Sequence[RegistryEntry]) -> None:
    """``confirm_entry_keys`` for many entries in one write."""
    await confirm_keys_bulk([key for e in entries for key in entry_claim_keys(e)])


async def release_entries_keys(
    entries: Sequence[RegistryEntry], token: str | None = None,
) -> None:
    """Release many entries' claims in a few writes; with the claiming
    request's token, only the claims that request inserted."""
    await release_keys_bulk([key for e in entries for key in entry_claim_keys(e)], token)


def releasable_synonyms(entry: RegistryEntry) -> list[Synonym]:
    """The synonyms retiring this entry releases: every active synonym except
    the auto-synonym (the entity's own address) and the keys a merge gave it
    in a merged-away entry's place (that entry's id and primary key). The
    primary key is not a synonym and always stays claimed — that is what lets
    a re-post of the same identity revive the retired entity instead of
    forking it; a merged-away entry's keys stay for the same reason."""
    return [
        s for s in entry.active_synonyms()
        if not entry.is_auto_synonym(s) and not s.merged_from
    ]


async def retire_entry_synonyms(
    entry: RegistryEntry, actor: EntryActor, *, dry_run: bool = False,
) -> list[Synonym]:
    """Retire an entry's synonyms: mark the releasable ones inactive and drop
    their claims, so they neither block another entity from taking the key nor
    resolve. They stay recorded on the entry for revival. Returns the synonyms
    released (would be released, on a dry run). Idempotent.

    The entry is written before the claims go: a crash in between leaves a
    claim its owner no longer backs, which inline healing frees at the next
    collision — never a released claim on a synonym still marked active.
    """
    if dry_run:
        return releasable_synonyms(entry)
    now = datetime.now(UTC)

    # Decided on the entry as written: a synonym another request adds or
    # removes meanwhile is kept or stays gone (entry_writes).
    def retire(fresh: RegistryEntry) -> object:
        released = releasable_synonyms(fresh)
        if not released:
            return UNCHANGED
        for syn in released:
            syn.status = "inactive"
        fresh.rebuild_search_values()
        actor.stamp(fresh, now)
        return released

    written, released = await apply_to_entry(
        entry.entry_id, retire, ("synonyms", "search_values", *STAMP_FIELDS),
        first=entry,
    )
    if written is None or released is UNCHANGED:
        return []
    retired = cast(list[Synonym], released)
    for syn in retired:
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
    return retired


def _synonym_scope(syn: Synonym) -> tuple[str, str, str]:
    return (syn.namespace, syn.entity_type, syn.composite_key_hash)


async def _claim_inactive_synonyms(
    entry: RegistryEntry, token: str,
) -> tuple[list[Synonym], list[tuple[Synonym, str]]]:
    """Claim, pending, every inactive synonym of ``entry`` whose key is free.
    Returns (claimed, [(synonym, holder entry_id), …] for keys another entry
    holds)."""
    claimed: list[Synonym] = []
    taken: list[tuple[Synonym, str]] = []
    for syn in entry.synonyms:
        if syn.status != "inactive":
            continue
        for attempt in (1, 2):
            try:
                await CompositeKeyClaim.claim(
                    syn.namespace, syn.entity_type, syn.composite_key_hash,
                    entry.entry_id, "synonym", state="pending", token=token,
                )
                claimed.append(syn)
                break
            except DuplicateKeyError:
                existing = await CompositeKeyClaim.find_existing(
                    syn.namespace, syn.entity_type, syn.composite_key_hash,
                )
                if attempt == 1 and await resolve_stale_claim(existing):
                    continue  # healed — retry once; index arbitrates races
                taken.append((syn, existing.owner_entry_id if existing else "?"))
                break
    return claimed, taken


def _revival(claimed: set[tuple[str, str, str]], actor: EntryActor):
    """The change a revival makes, applied to the entry as written
    (entry_writes): a claimed synonym that another request removed meanwhile
    is not brought back. Returns the synonyms revived, or UNCHANGED."""
    now = datetime.now(UTC)

    def revive(fresh: RegistryEntry) -> object:
        revived = [
            s for s in fresh.synonyms
            if s.status == "inactive" and _synonym_scope(s) in claimed
        ]
        if not revived:
            return UNCHANGED
        for syn in revived:
            syn.status = "active"
        fresh.rebuild_search_values()
        actor.stamp(fresh, now)
        return revived

    return revive


async def _release_synonym_claims(entry_id: str, synonyms: list[Synonym], token: str) -> None:
    if synonyms:
        await release_keys_bulk(
            [(*_synonym_scope(s), entry_id, "synonym") for s in synonyms], token,
        )


async def revive_entry_synonyms(
    entry: RegistryEntry, actor: EntryActor,
) -> tuple[list[Synonym], list[tuple[Synonym, str]]]:
    """Revive an entry's inactive synonyms: re-claim each one whose key is
    free. One another entity took while this one was retired stays inactive —
    the entity is revived without it. Returns (reclaimed, [(synonym, holder
    entry_id), …] for those left inactive). Idempotent.

    Same two-phase order as a synonym add: claim pending, write the entry,
    confirm. A claim whose synonym another request removed before the write
    is released.
    """
    if entry.merged_into:
        # A merged-away entry's keys belong to the entry it was merged into.
        return [], []
    token = new_claim_token()
    claimed, taken = await _claim_inactive_synonyms(entry, token)
    if not claimed:
        return claimed, taken

    try:
        _written, revived = await apply_to_entry(
            entry.entry_id, _revival({_synonym_scope(s) for s in claimed}, actor),
            ("synonyms", "search_values", *STAMP_FIELDS), first=entry,
        )
    except Exception:
        await _release_synonym_claims(entry.entry_id, claimed, token)
        raise
    kept = set() if revived in (UNCHANGED, None) else {_synonym_scope(s) for s in revived}
    await _release_synonym_claims(
        entry.entry_id, [s for s in claimed if _synonym_scope(s) not in kept], token,
    )
    reclaimed = [s for s in claimed if _synonym_scope(s) in kept]
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
        # The observed state and date in the filter: a request that took the
        # claim over since re-dated it, and its claim is in flight again. Only
        # what the filter matched is counted.
        observed = {"_id": doc["_id"], "state": "pending", "created_at": doc["created_at"]}
        if backed:
            result = await claims_coll.update_one(
                observed, {"$set": {"state": "confirmed", "claim_token": None}},
            )
            summary["confirmed"] += result.modified_count
        else:
            result = await claims_coll.delete_one(observed)
            summary["deleted"] += result.deleted_count

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
        losers: set[tuple[str, str, str]] = set()
        for syn in entry.synonyms:
            if not syn.composite_key_hash or syn.status == "inactive":
                continue
            try:
                await CompositeKeyClaim.claim(
                    syn.namespace, syn.entity_type, syn.composite_key_hash,
                    entry.entry_id, "synonym",
                )
                summary["synonym_claimed"] += 1
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
                losers.add((syn.namespace, syn.entity_type, syn.composite_key_hash))
        if losers:
            def strip(fresh: RegistryEntry, losers=losers) -> None:
                fresh.synonyms = [
                    s for s in fresh.synonyms
                    if s.status == "inactive"
                    or (s.namespace, s.entity_type, s.composite_key_hash) not in losers
                ]
                fresh.rebuild_search_values()

            await apply_to_entry(entry.entry_id, strip, ("synonyms", "search_values"), first=entry)

    logger.info("CASE-427 backfill complete: %s", summary)
    return summary


async def reconcile_orphan_claims() -> dict[str, int]:
    """Full-collection audit: delete claims whose backing entry/synonym no
    longer exists. ADMIN VERB — not on the startup path since the two-phase
    protocol (CASE-554); day-to-day consistency is kept by
    reconcile_pending_claims + inline healing. Its old hazard against live
    traffic, deleting a claim an in-flight request was about to back
    (CASE-553), is closed by the grace window and the state-and-date filter
    below. It stays manual because it scans the whole collection, and its
    entrypoint (registry.admin_backfill_claims) first runs backfill_claims,
    which strips duplicate synonyms from entries.

    Idempotent — only deletes dangling claims, never mutates entries.
    Catches confirmed-dangling residue in bulk: claims for deleted entries,
    cross-namespace-synonym residue after namespace deletion, stale claims
    from entry mutations. A pending claim younger than the grace window is
    an in-flight request's (its entry is written after it) and is never
    read as dangling, and each delete names the state and date the scan
    saw, so a claim a request takes over meanwhile survives.

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

    cutoff = datetime.now(UTC) - timedelta(seconds=PENDING_GRACE_SECONDS)
    orphans = []
    for doc in batch:
        created = doc.get("created_at")
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if doc.get("state") == "pending" and created is not None and created >= cutoff:
            continue  # in flight: its entry is written after its claim
        entry = existing.get(doc["owner_entry_id"])
        if entry is None or not _entry_doc_backs_claim(entry, doc):
            # Legacy claims carry no state; {"state": None} matches the
            # missing field.
            orphans.append({
                "_id": doc["_id"],
                "state": doc.get("state"),
                "created_at": doc.get("created_at"),
            })

    if not orphans:
        return 0
    result = await claims_coll.delete_many({"$or": orphans})
    return int(result.deleted_count)


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
