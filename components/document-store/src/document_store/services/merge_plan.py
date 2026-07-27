"""Merge-restore planning — what a merge would do, decided before it writes.

A plain restore refuses a non-empty target: every archived row is new, so
"insert everything" is always the right answer. A *merge* restore takes the
archive as a delta source against a namespace that already holds data, which
means every archived entity first has to be classified:

- **insert** — the target has no counterpart, so the row goes in untouched.
- **clash** — the target already holds this entity; what happens next is the
  caller's per-request policy, not the platform's.
- **conflict** — the archive and the target disagree about *identity itself*.
  Never resolved silently (see :class:`MergePlanner` for why).

The classification is this module's whole job. It runs before any write, which
is also what makes a merge dry run meaningful: the plan IS the dry run.

Matching is deliberately two-keyed. Every entity type has a canonical **ID**
(``document_id``, ``template_id``, …) and a **logical key** — the natural key
that says which real-world thing it is (a terminology's ``value``, a document's
``(template_id, identity_hash)``). Merge v1 is a same-install operation: IDs are
preserved end to end, so the two keys must agree. Where they don't, the archive
came from somewhere else and is wearing same-install clothes — that is the
cross-install merge case, which needs ID re-minting and is out of scope here.

Reads are bulk and indexed: the target is probed by batched key queries derived
from the archive's own keys, so the cost scales with the delta being merged
rather than with the size of the namespace receiving it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("document_store.merge_plan")

# How many keys travel in one target probe. Large enough that a delta of a few
# thousand entities costs single-digit round trips, small enough to stay well
# inside MongoDB's document size limit for the query itself.
KEY_BATCH_SIZE = 500


@dataclass(frozen=True)
class EntitySpec:
    """How one entity type is identified when merging.

    ``id_fields`` is the canonical handle; ``logical_fields`` is the natural
    key. Either may be empty: term relations have no ID of their own (the
    relation *is* its endpoints), and an entity whose logical key is absent
    from the row — a registry entry with an empty composite-key hash, an
    identity-less document — is matched by ID alone.
    """

    id_fields: tuple[str, ...]
    logical_fields: tuple[str, ...]
    # Fields worth reading back from the target when deciding what to do with
    # a clash. Empty means "the whole row" — schema entities need every field
    # to diff against the archive's version.
    projection: tuple[str, ...] = ()


# Templates and documents carry a version dimension; both are keyed here on the
# logical entity, and versions are resolved by the caller applying the plan
# (a template clash compares schemas per version; a document clash resolves
# against the target's head version).
MERGE_ENTITY_SPECS: dict[str, EntitySpec] = {
    "terminologies": EntitySpec(("terminology_id",), ("value",)),
    "terms": EntitySpec(("term_id",), ("terminology_id", "value")),
    "term_relations": EntitySpec(
        (), ("source_term_id", "target_term_id", "relation_type")
    ),
    "templates": EntitySpec(("template_id", "version"), ("value", "version")),
    "documents": EntitySpec(
        ("document_id",),
        ("template_id", "identity_hash"),
        projection=(
            "document_id",
            "version",
            "template_id",
            "template_version",
            "identity_hash",
            "status",
            # The `newer` clash policy compares this against the archive's;
            # projecting it away would silently make every comparison
            # unresolvable and fall back to keeping the target.
            "updated_at",
        ),
    ),
    "files": EntitySpec(("file_id",), ("checksum",)),
    "registry_entries": EntitySpec(
        ("entry_id",), ("entity_type", "primary_composite_key_hash")
    ),
}

# Bookkeeping that legitimately differs between two copies of the same entity
# and must not read as a schema difference. ``_id`` is stripped on read;
# the audit fields move whenever anything touches a row.
_NON_SCHEMA_FIELDS = frozenset({
    "_id",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
    "last_synced_at",
})


@dataclass
class Clash:
    """An archive entity the target already holds."""

    entity: dict[str, Any]
    # Every target row the archived entity matched. More than one only for
    # versioned types, where the caller resolves against the head version.
    targets: list[dict[str, Any]]
    # Empty for an identical pair — the archive adds nothing and every policy
    # agrees to leave the target alone. Populated when the two differ, keyed
    # by field name with both sides' values.
    diff: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def target(self) -> dict[str, Any]:
        return self.targets[0]

    @property
    def identical(self) -> bool:
        return not self.diff


@dataclass
class Conflict:
    """The archive and the target disagree about identity.

    ``reason`` is written for an operator reading a failed merge report, not
    for a branch in code — a conflict is always terminal for its item.
    """

    entity: dict[str, Any]
    reason: str


@dataclass
class EntityPlan:
    """What a merge would do to one entity type."""

    entity_type: str
    to_insert: list[dict[str, Any]] = field(default_factory=list)
    clashes: list[Clash] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    # old id -> surviving target id, for entities the target already holds
    # under a different ID. Populated by matching; consumed by the caller to
    # rewrite whatever pointed at the archive's copy.
    mapping: dict[str, str] = field(default_factory=dict)
    # False when the target was read through a projection, so the two sides
    # cannot be compared field by field. Such a clash is never "unchanged" —
    # the archive may well carry different content, and the caller's policy
    # decides without a diff.
    diffable: bool = True

    @property
    def identical_clashes(self) -> list[Clash]:
        if not self.diffable:
            return []
        return [c for c in self.clashes if c.identical]

    @property
    def differing_clashes(self) -> list[Clash]:
        if not self.diffable:
            return list(self.clashes)
        return [c for c in self.clashes if not c.identical]

    def summary(self) -> dict[str, int]:
        counts = {
            "insert": len(self.to_insert),
            "unchanged": len(self.identical_clashes),
            "clash": len(self.differing_clashes),
            "conflict": len(self.conflicts),
        }
        if self.mapping:
            counts["remapped"] = len(self.mapping)
        return counts


def diff_entities(
    archive_entity: dict[str, Any],
    target_entity: dict[str, Any],
    *,
    ignore: Iterable[str] = (),
) -> dict[str, dict[str, Any]]:
    """Field-level difference between two copies of the same entity.

    Audit and bookkeeping fields are excluded: two faithful copies of one
    terminology differ in ``updated_at`` and that is not a schema difference.
    Comparison is on stored form, which is exact here because merge v1 is
    same-install — both sides were written by the same services, so a
    value-vs-canonical-ID mismatch (the reason template-store resolves
    references before comparing) cannot arise.
    """
    skip = _NON_SCHEMA_FIELDS | set(ignore)
    diff: dict[str, dict[str, Any]] = {}
    for key in sorted(set(archive_entity) | set(target_entity)):
        if key in skip:
            continue
        old = target_entity.get(key)
        new = archive_entity.get(key)
        if old != new:
            diff[key] = {"target": old, "archive": new}
    return diff


def key_of(
    entity: dict[str, Any], fields: Sequence[str]
) -> tuple[Any, ...] | None:
    """Key tuple for ``entity``, or None when the key does not apply.

    A missing or empty component means the entity has no key of this kind —
    an identity-less document, a legacy registry entry registered with an
    empty composite key. Those are matched on their other key rather than
    being force-matched on a partial one, which would collide everything that
    shares the same blank.
    """
    if not fields:
        return None
    values: list[Any] = []
    for name in fields:
        value = entity.get(name)
        if value is None or value == "":
            return None
        values.append(value)
    return tuple(values)


class MergePlanner:
    """Classifies an archive's entities against a live target namespace.

    One instance plans one namespace. The Mongo client is used for reads only;
    nothing here writes, which is what lets the same code back both the dry run
    and the real merge's first phase.
    """

    def __init__(self, mongo_client: Any, collection_map: dict[str, tuple[str, str]]):
        self._mongo = mongo_client
        self._collection_map = collection_map

    async def plan(
        self,
        entity_type: str,
        entities: list[dict[str, Any]],
        namespace: str,
    ) -> EntityPlan:
        """Classify every archived entity of one type against the target."""
        spec = MERGE_ENTITY_SPECS[entity_type]
        projection = (
            {name: 1 for name in spec.projection} if spec.projection else None
        )
        plan = EntityPlan(entity_type=entity_type, diffable=projection is None)
        if not entities:
            return plan

        db_name, coll_name = self._collection_map[entity_type]
        collection = self._mongo[db_name][coll_name]

        id_index = await self._fetch_by_keys(
            collection, namespace, spec.id_fields,
            self._keys(entities, spec.id_fields), projection,
        )
        logical_index = await self._fetch_by_keys(
            collection, namespace, spec.logical_fields,
            self._keys(entities, spec.logical_fields), projection,
        )

        for entity in entities:
            id_key = key_of(entity, spec.id_fields)
            logical_key = key_of(entity, spec.logical_fields)
            by_id = id_index.get(id_key) if id_key is not None else None
            by_logical = (
                logical_index.get(logical_key) if logical_key is not None else None
            )

            conflict = self._identity_conflict(
                entity, spec, id_key, logical_key, by_id, by_logical
            )
            if conflict is not None:
                plan.conflicts.append(conflict)
                continue

            # The target holds this entity under a different ID. That is a
            # collision like any other — resolution is the caller's policy —
            # but it also means everything pointing at the archive's copy has
            # to be repointed, so the surviving ID is recorded.
            if by_logical and not by_id and id_key is not None:
                surviving = key_of(by_logical[0], spec.id_fields)
                if surviving is not None and surviving != id_key:
                    plan.mapping[str(id_key[0])] = str(surviving[0])

            targets = by_id or by_logical
            if not targets:
                plan.to_insert.append(entity)
            else:
                plan.clashes.append(
                    Clash(
                        entity=entity,
                        targets=targets,
                        # A projected read cannot support a field-level diff —
                        # the fields it omitted would all read as differences.
                        # Types that need diffing declare no projection.
                        diff={} if projection else diff_entities(entity, targets[0]),
                    )
                )

        return plan

    def _identity_conflict(
        self,
        entity: dict[str, Any],
        spec: EntitySpec,
        id_key: tuple[Any, ...] | None,
        logical_key: tuple[Any, ...] | None,
        by_id: list[dict[str, Any]] | None,
        by_logical: list[dict[str, Any]] | None,
    ) -> Conflict | None:
        """Detect an archive/target disagreement about which thing is which.

        Matching is by content, so the same entity under two different IDs is
        expected and is handled as a match. What is never expected is the
        reverse: one ID naming two different logical entities. Whether the
        archive came from this install or another, that means an ID has been
        reused for something else, and no automatic resolution is defensible —
        picking either side silently destroys one of the two identities.
        """
        if id_key is None or logical_key is None:
            return None  # only one key applies — nothing to disagree about
        if by_id and not by_logical:
            return Conflict(
                entity=entity,
                reason=(
                    f"target already uses {self._describe(spec.id_fields, id_key)} "
                    f"for a different entity — the archive's copy has "
                    f"{self._describe(spec.logical_fields, logical_key)}, the "
                    f"target's has "
                    f"{self._describe(spec.logical_fields, key_of(by_id[0], spec.logical_fields) or ())}"
                ),
            )
        if by_id and by_logical:
            id_of_logical = key_of(by_logical[0], spec.id_fields)
            if id_of_logical != id_key:
                return Conflict(
                    entity=entity,
                    reason=(
                        f"{self._describe(spec.id_fields, id_key)} and "
                        f"{self._describe(spec.logical_fields, logical_key)} "
                        "match two different rows in the target"
                    ),
                )
        return None

    @staticmethod
    def _describe(fields: Sequence[str], key: Sequence[Any]) -> str:
        return "(" + ", ".join(
            f"{name}={value!r}" for name, value in zip(fields, key, strict=False)
        ) + ")"

    @staticmethod
    def _keys(
        entities: list[dict[str, Any]], fields: Sequence[str]
    ) -> list[tuple[Any, ...]]:
        seen: dict[tuple[Any, ...], None] = {}
        for entity in entities:
            key = key_of(entity, fields)
            if key is not None:
                seen[key] = None
        return list(seen)

    async def _fetch_by_keys(
        self,
        collection: Any,
        namespace: str,
        fields: Sequence[str],
        keys: list[tuple[Any, ...]],
        projection: dict[str, int] | None,
    ) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
        """Read the target rows matching any of ``keys``, batched.

        Returns key → rows: a list because versioned types (documents,
        templates keyed without their version) legitimately have several rows
        per key, and the caller decides which one a clash resolves against.
        """
        index: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        if not fields or not keys:
            return index

        for start in range(0, len(keys), KEY_BATCH_SIZE):
            batch = keys[start:start + KEY_BATCH_SIZE]
            query: dict[str, Any]
            if len(fields) == 1:
                query = {"namespace": namespace, fields[0]: {"$in": [k[0] for k in batch]}}
            else:
                query = {
                    "namespace": namespace,
                    "$or": [
                        dict(zip(fields, key, strict=False)) for key in batch
                    ],
                }
            cursor = (
                collection.find(query, projection)
                if projection
                else collection.find(query)
            )
            async for row in cursor:
                row.pop("_id", None)
                key = key_of(row, fields)
                if key is not None:
                    index.setdefault(key, []).append(row)

        return index
