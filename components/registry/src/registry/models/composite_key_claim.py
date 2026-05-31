"""Composite-key uniqueness claims (CASE-427).

The single uniqueness domain for composite keys in the Registry. Fed by two
sources — canonical/primary composite keys AND synonym hashes — so that
``(namespace, entity_type, composite_key_hash)`` is unique across the whole
Registry regardless of whether a given hash arrives as an entry's primary key
or as a synonym.

Why this exists: synonyms are stored as embedded subdocuments in
``RegistryEntry.synonyms`` (see entry.py), which a MongoDB unique index cannot
guard the way it guards the primary key. ``add_synonyms`` was therefore a
check-then-insert TOCTOU — concurrent claims of the same hash against different
entries all succeeded. This collection makes the claim a DB-atomic
insert-or-reject: the unique index *is* the lock. MongoDB here is a single
instance (no replica set → no multi-document transactions), so a unique index
is the only atomic primitive available — exactly how the primary key is already
protected by ``namespace_entity_keyhash_unique_idx`` on RegistryEntry.

This collection is a WRITE-TIME GATE, not the read model. ``RegistryEntry``
remains authoritative for resolution/search/export; the embedded ``synonyms``
array is untouched. Claims are kept in lockstep with that read model by the
write paths + an idempotent reconciliation pass.
"""

from datetime import UTC, datetime
from typing import ClassVar

from beanie import Document
from pydantic import Field
from pymongo import IndexModel
from pymongo.errors import DuplicateKeyError


class CompositeKeyClaim(Document):
    """One claim that a composite-key hash is owned by a single entry."""

    namespace: str = Field(
        ...,
        description="Namespace the claimed key belongs to (the synonym's own "
                    "namespace for synonym claims, the entry's for primary claims)"
    )
    entity_type: str = Field(
        ...,
        description="Entity type (terminologies, terms, templates, documents, files)"
    )
    composite_key_hash: str = Field(
        ...,
        description="SHA-256 hash of the composite key. Never empty — empty "
                    "keys are not claimed."
    )
    owner_entry_id: str = Field(
        ...,
        description="entry_id of the RegistryEntry that owns this key. The "
                    "stable, globally-unique business key (not the Mongo _id)."
    )
    kind: str = Field(
        ...,
        description='"primary" | "synonym" — which source fed the claim. '
                    "Diagnostic + drives the migration winner policy "
                    "(primary beats synonym); NOT part of the unique key."
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )

    class Settings:
        name = "composite_key_claims"
        indexes: ClassVar = [
            # The uniqueness gate: one owner per (namespace, entity_type, hash),
            # across primary AND synonym claims alike. partialFilterExpression
            # mirrors RegistryEntry's namespace_entity_keyhash_unique_idx so
            # empty hashes are exempt (they are never inserted anyway).
            IndexModel(
                [("namespace", 1), ("entity_type", 1), ("composite_key_hash", 1)],
                unique=True,
                partialFilterExpression={"composite_key_hash": {"$gt": ""}},
                name="claim_ns_type_hash_unique_idx",
            ),
            # Release-by-owner (entry delete) and merge-transfer want all claims
            # for a given entry cheaply.
            IndexModel(
                [("owner_entry_id", 1)],
                name="claim_owner_idx",
            ),
        ]

    # ── Atomic primitives ──────────────────────────────────────────────
    # Raw motor ops (like IdCounter) so the unique index does the locking and
    # DuplicateKeyError propagates cleanly.

    @classmethod
    async def claim(
        cls,
        namespace: str,
        entity_type: str,
        composite_key_hash: str,
        owner_entry_id: str,
        kind: str,
    ) -> "CompositeKeyClaim | None":
        """Atomically claim a composite-key hash for an owner.

        Returns the claim on success. Empty hashes are not claimed (returns
        None). Idempotent self-retry: if the hash is already claimed by the
        SAME owner, returns the existing claim (no-op) rather than raising.
        Raises ``DuplicateKeyError`` only when the hash is claimed by a
        DIFFERENT owner — the caller inspects ``find_existing`` to decide.
        """
        if not composite_key_hash:
            return None
        doc = {
            "namespace": namespace,
            "entity_type": entity_type,
            "composite_key_hash": composite_key_hash,
            "owner_entry_id": owner_entry_id,
            "kind": kind,
            "created_at": datetime.now(UTC),
        }
        try:
            await cls.get_motor_collection().insert_one(doc)
        except DuplicateKeyError:
            existing = await cls.find_existing(namespace, entity_type, composite_key_hash)
            if existing is not None and existing.owner_entry_id == owner_entry_id:
                return existing  # self-retry — already ours
            raise
        return cls(
            namespace=namespace,
            entity_type=entity_type,
            composite_key_hash=composite_key_hash,
            owner_entry_id=owner_entry_id,
            kind=kind,
            created_at=doc["created_at"],
        )

    @classmethod
    async def find_existing(
        cls, namespace: str, entity_type: str, composite_key_hash: str
    ) -> "CompositeKeyClaim | None":
        """Return the claim for a (namespace, entity_type, hash), or None."""
        return await cls.find_one({
            "namespace": namespace,
            "entity_type": entity_type,
            "composite_key_hash": composite_key_hash,
        })

    @classmethod
    async def release(
        cls,
        namespace: str,
        entity_type: str,
        composite_key_hash: str,
        owner_entry_id: str | None = None,
    ) -> None:
        """Delete a claim. If owner_entry_id is given, only delete the claim
        when it is actually owned by that entry (never release someone
        else's claim)."""
        if not composite_key_hash:
            return
        query: dict = {
            "namespace": namespace,
            "entity_type": entity_type,
            "composite_key_hash": composite_key_hash,
        }
        if owner_entry_id is not None:
            query["owner_entry_id"] = owner_entry_id
        await cls.get_motor_collection().delete_one(query)

    @classmethod
    async def release_for_owner(cls, owner_entry_id: str) -> int:
        """Delete every claim owned by an entry (entry hard-delete). Returns
        the number removed."""
        result = await cls.get_motor_collection().delete_many(
            {"owner_entry_id": owner_entry_id}
        )
        return result.deleted_count

    @classmethod
    async def transfer(
        cls,
        namespace: str,
        entity_type: str,
        composite_key_hash: str,
        new_owner_entry_id: str,
    ) -> None:
        """Re-point an existing claim to a new owner (merge: deprecated →
        preferred). Marks it kind=synonym since it now lives as a synonym of
        the preferred entry."""
        await cls.get_motor_collection().update_one(
            {
                "namespace": namespace,
                "entity_type": entity_type,
                "composite_key_hash": composite_key_hash,
            },
            {"$set": {"owner_entry_id": new_owner_entry_id, "kind": "synonym"}},
        )
