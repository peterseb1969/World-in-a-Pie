"""Composite-key claim orchestration (CASE-427).

Higher-level operations over the ``CompositeKeyClaim`` collection: the one-shot
**backfill** that builds the claims domain from existing ``RegistryEntry`` data
(reconciling pre-existing duplicates), and the idempotent **reconciliation**
that prunes orphan claims. The atomic per-claim primitives live on the model
(``CompositeKeyClaim.claim/release/transfer``).

Backfill is destructive (it strips losing duplicate synonyms) and is therefore
an explicit one-shot, not a silent every-boot step. Reconciliation is
non-destructive to entries (only deletes dangling claims) and is safe to run at
startup.
"""

import logging

from pymongo.errors import DuplicateKeyError

from ..models.composite_key_claim import CompositeKeyClaim
from ..models.entry import RegistryEntry

logger = logging.getLogger("registry.claims")


async def claim_entry_keys(entry: RegistryEntry) -> None:
    """Claim an entry's primary key + all embedded synonym hashes into the
    unified domain (CASE-427), best-effort.

    Used by the create paths AFTER a successful insert. Primary-vs-primary
    uniqueness is already enforced atomically by RegistryEntry's
    namespace_entity_keyhash_unique_idx, so this claim's role is to populate
    the unified domain so later add_synonyms conflict with these keys
    (cross-feed protection). A DuplicateKeyError here means the hash is already
    claimed elsewhere — a rare cross-feed/concurrent-window case; log it for
    reconciliation rather than failing the (already-committed) entry.
    """
    pairs = [
        (entry.namespace, entry.entity_type, entry.primary_composite_key_hash, "primary"),
    ]
    for syn in entry.synonyms:
        pairs.append((syn.namespace, syn.entity_type, syn.composite_key_hash, "synonym"))
    for ns, etype, key_hash, kind in pairs:
        if not key_hash:
            continue
        try:
            await CompositeKeyClaim.claim(ns, etype, key_hash, entry.entry_id, kind)
        except DuplicateKeyError:
            existing = await CompositeKeyClaim.find_existing(ns, etype, key_hash)
            owner = existing.owner_entry_id if existing else "?"
            logger.warning(
                "CASE-427: %s key %s (%s/%s) for new entry %s already claimed "
                "by %s — cross-feed/concurrent collision, left for reconciliation.",
                kind, key_hash, ns, etype, entry.entry_id, owner,
            )
        except Exception:
            # CASE-431: truly best-effort. The entry is already inserted and its
            # primary uniqueness is guaranteed by namespace_entity_keyhash_unique_idx;
            # the claim is only cross-feed protection for future add_synonyms. Any
            # non-DuplicateKeyError hiccup (transient Mongo error, or an
            # uninitialised collection in a misconfigured harness) must NOT fail
            # the committed entry — log it and leave the missing claim for startup
            # reconciliation.
            logger.exception(
                "CASE-431: %s key %s (%s/%s) claim for committed entry %s failed "
                "unexpectedly — entry stands; missing claim left for reconciliation.",
                kind, key_hash, ns, etype, entry.entry_id,
            )


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
            if not syn.composite_key_hash:
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
    """Delete claims whose backing entry/synonym no longer exists.

    Safe + idempotent — only deletes dangling claims, never mutates entries.
    Run at startup (like recover_incomplete_deletions). Catches: claims for
    deleted entries, cross-namespace-synonym residue after namespace deletion,
    and orphans left by a claim-insert that succeeded while the subsequent
    entry write failed (no-transaction window).
    """
    summary = {"checked": 0, "orphans_deleted": 0}
    async for claim in CompositeKeyClaim.find_all():
        summary["checked"] += 1
        entry = await RegistryEntry.find_one({"entry_id": claim.owner_entry_id})
        if entry is None:
            await _delete_claim(claim)
            summary["orphans_deleted"] += 1
            continue
        if not _entry_backs_claim(entry, claim):
            await _delete_claim(claim)
            summary["orphans_deleted"] += 1
    if summary["orphans_deleted"]:
        logger.info("CASE-427 reconcile: %s", summary)
    return summary


def _entry_backs_claim(entry: RegistryEntry, claim: CompositeKeyClaim) -> bool:
    """True if the entry still owns the claimed (namespace, entity_type, hash)
    as either its primary key or one of its embedded synonyms."""
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
        for s in entry.synonyms
    )


async def _delete_claim(claim: CompositeKeyClaim) -> None:
    await CompositeKeyClaim.get_motor_collection().delete_one(
        {
            "namespace": claim.namespace,
            "entity_type": claim.entity_type,
            "composite_key_hash": claim.composite_key_hash,
        }
    )
