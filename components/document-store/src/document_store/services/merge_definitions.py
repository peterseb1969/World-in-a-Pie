"""Definitions compatibility — the precondition a document merge runs behind.

Terminologies, terms and templates are not resolved item by item while
documents are being written. They are what makes writing documents *possible*:
a document merged under a template whose schema differs between the two sides
is a document validated against the wrong contract. So a merge checks the
definitions first, refuses if they disagree, and only then moves data.

The check is by **content, not by UUID**. Two installs that independently
created `GENDER` hold it under two different canonical IDs, and the same
install restoring its own archive holds it under one; comparing content covers
both without the caller having to declare which situation they are in. The ID
mapping the document pass needs falls out of the same work — establishing that
the target's `GENDER` *is* the archive's `GENDER` is the act of learning
``A-uuid -> B-uuid``.

Verify-only is the default. Changing a live namespace's definitions is an
active decision, never a side effect of restoring data into it, so the two
strategies that would change them are opt-in:

``add_missing`` — insert terminologies and templates the target does not have.
``extend_terminologies`` — add terms the target's terminology is missing.

Without them, anything the target lacks is an incompatibility and the merge
refuses rather than quietly importing half a vocabulary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("document_store.merge_definitions")

# Entity types this pass owns, in dependency order: a term's identity contains
# its terminology, so terminologies must be resolved first.
DEFINITION_TYPES = ("terminologies", "terms", "templates")

# The natural key each definition is recognised by — the name a human would
# use, never the UUID.
VALUE_KEYS: dict[str, tuple[str, ...]] = {
    "terminologies": ("value",),
    "terms": ("terminology_id", "value"),
    "templates": ("value", "version"),
}

# The canonical ID each type maps old->new on.
ID_KEYS: dict[str, str] = {
    "terminologies": "terminology_id",
    "terms": "term_id",
    "templates": "template_id",
}

# Bookkeeping that differs between two honest copies of the same definition.
_AUDIT_FIELDS = frozenset({
    "_id", "created_at", "created_by", "updated_at", "updated_by",
    "last_synced_at", "namespace",
})

# Identity and linkage, compared through the natural key or the mapping rather
# than byte-for-byte: a UUID difference is the normal case here, not a finding.
_ID_FIELDS = frozenset({
    "terminology_id", "term_id", "template_id", "entry_id",
})

# Vocabulary metadata. A difference here does not make two definitions
# incompatible — it is cosmetic, the target's wins, and it gets reported so an
# operator can see what their side kept.
_COSMETIC_FIELDS = frozenset({
    "label", "description", "aliases", "display_order", "definition",
})


@dataclass
class Incompatibility:
    """A definition the merge cannot proceed past."""

    entity_type: str
    name: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class TargetWins:
    """A cosmetic difference resolved in the target's favour.

    Never fatal, always reported: silently rewriting (or silently discarding)
    a label or an alias list is the kind of change an operator finds out about
    much later, from a UI that no longer says what they expect.
    """

    entity_type: str
    name: str
    fields: list[str]
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class DefinitionsPlan:
    """What pass 1 found, and what pass 2 needs from it."""

    # old canonical id -> surviving canonical id, per entity type. Entries
    # appear only where the two sides use different IDs for the same thing.
    mapping: dict[str, dict[str, str]] = field(
        default_factory=lambda: {t: {} for t in DEFINITION_TYPES}
    )
    # Definitions the target lacks, insertable only under the opt-in flags.
    to_add: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {t: [] for t in DEFINITION_TYPES}
    )
    incompatibilities: list[Incompatibility] = field(default_factory=list)
    target_wins: list[TargetWins] = field(default_factory=list)
    # Definitions present and identical on both sides — nothing to do.
    unchanged: dict[str, int] = field(
        default_factory=lambda: {t: 0 for t in DEFINITION_TYPES}
    )

    @property
    def compatible(self) -> bool:
        return not self.incompatibilities

    def summary(self) -> dict[str, dict[str, int]]:
        return {
            entity_type: {
                "unchanged": self.unchanged[entity_type],
                "add": len(self.to_add[entity_type]),
                "mapped": len(self.mapping[entity_type]),
            }
            for entity_type in DEFINITION_TYPES
        }


def compare_definitions(
    archive: dict[str, Any], target: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """Split a definition's differences into cosmetic and substantive.

    Returns ``(cosmetic, substantive)`` field-name lists. Audit fields and
    canonical IDs are excluded from both: two honest copies of one definition
    differ in ``updated_at`` and (across installs) in their UUIDs, and neither
    is a finding.
    """
    cosmetic: list[str] = []
    substantive: list[str] = []
    for key in sorted(set(archive) | set(target)):
        if key in _AUDIT_FIELDS or key in _ID_FIELDS:
            continue
        if archive.get(key) == target.get(key):
            continue
        (cosmetic if key in _COSMETIC_FIELDS else substantive).append(key)
    return cosmetic, substantive


class DefinitionsPlanner:
    """Builds a :class:`DefinitionsPlan` for one namespace. Reads only."""

    def __init__(
        self,
        mongo_client: Any,
        collection_map: dict[str, tuple[str, str]],
        *,
        add_missing: bool = False,
        extend_terminologies: bool = False,
    ):
        self._mongo = mongo_client
        self._collection_map = collection_map
        self._add_missing = add_missing
        self._extend_terminologies = extend_terminologies

    async def plan(
        self,
        entities_by_type: dict[str, list[dict[str, Any]]],
        namespace: str,
    ) -> DefinitionsPlan:
        """Check the archive's definitions against the target's."""
        plan = DefinitionsPlan()

        for entity_type in DEFINITION_TYPES:
            entities = entities_by_type.get(entity_type) or []
            if not entities:
                continue
            target_index = await self._index_target(entity_type, namespace)
            for entity in entities:
                self._classify(entity_type, entity, target_index, plan)

        return plan

    def _classify(
        self,
        entity_type: str,
        entity: dict[str, Any],
        target_index: dict[tuple[Any, ...], dict[str, Any]],
        plan: DefinitionsPlan,
    ) -> None:
        key = self._natural_key(entity_type, entity, plan)
        name = self._describe(entity_type, entity)
        target = target_index.get(key) if key is not None else None

        if target is None:
            self._classify_missing(entity_type, entity, name, plan)
            return

        # Present on both sides: record the mapping, then compare content.
        old_id = entity.get(ID_KEYS[entity_type])
        new_id = target.get(ID_KEYS[entity_type])
        if old_id and new_id and old_id != new_id:
            plan.mapping[entity_type][str(old_id)] = str(new_id)

        cosmetic, substantive = compare_definitions(entity, target)
        if substantive:
            plan.incompatibilities.append(
                Incompatibility(
                    entity_type=entity_type,
                    name=name,
                    reason=(
                        "exists on both sides with different content "
                        f"({', '.join(substantive)})"
                    ),
                    detail={
                        field_name: {
                            "target": target.get(field_name),
                            "archive": entity.get(field_name),
                        }
                        for field_name in substantive
                    },
                )
            )
            return

        if cosmetic:
            plan.target_wins.append(
                TargetWins(
                    entity_type=entity_type,
                    name=name,
                    fields=cosmetic,
                    detail={
                        field_name: {
                            "kept": target.get(field_name),
                            "discarded": entity.get(field_name),
                        }
                        for field_name in cosmetic
                    },
                )
            )
        plan.unchanged[entity_type] += 1

    def _classify_missing(
        self,
        entity_type: str,
        entity: dict[str, Any],
        name: str,
        plan: DefinitionsPlan,
    ) -> None:
        """The target does not have this definition — addable, or a refusal."""
        allowed = (
            self._extend_terminologies
            if entity_type == "terms"
            else self._add_missing
        )
        if allowed:
            plan.to_add[entity_type].append(entity)
            return

        flag = (
            "extend_terminologies" if entity_type == "terms" else "add_missing"
        )
        plan.incompatibilities.append(
            Incompatibility(
                entity_type=entity_type,
                name=name,
                reason=(
                    "the target does not have it — merging would leave the "
                    f"archive's documents referencing nothing. Set {flag}=true "
                    "to add it, or reconcile the two sides first"
                ),
            )
        )

    def _natural_key(
        self, entity_type: str, entity: dict[str, Any], plan: DefinitionsPlan
    ) -> tuple[Any, ...] | None:
        """The key this definition is recognised by on the target side.

        A term's key contains its terminology, so it is resolved through the
        mapping the terminology pass just produced — which is why the types
        are walked in dependency order.
        """
        values: list[Any] = []
        for field_name in VALUE_KEYS[entity_type]:
            value = entity.get(field_name)
            if value is None or value == "":
                return None
            if field_name == "terminology_id":
                value = plan.mapping["terminologies"].get(str(value), value)
            values.append(value)
        return tuple(values)

    @staticmethod
    def _describe(entity_type: str, entity: dict[str, Any]) -> str:
        """Name a definition the way an operator reading a refusal would."""
        if entity_type == "templates":
            return f"{entity.get('value')} v{entity.get('version')}"
        if entity_type == "terms":
            return f"{entity.get('value')} (in {entity.get('terminology_id')})"
        return str(entity.get("value"))

    async def _index_target(
        self, entity_type: str, namespace: str
    ) -> dict[tuple[Any, ...], dict[str, Any]]:
        """Read the target's definitions of one type, keyed by natural key.

        A whole-type read rather than a batched probe: definitions are the
        small collections (vocabularies and schemas, not data), and pass 1
        needs to answer "does the target have one of these?" for every
        archived definition anyway.
        """
        db_name, coll_name = self._collection_map[entity_type]
        index: dict[tuple[Any, ...], dict[str, Any]] = {}
        cursor = self._mongo[db_name][coll_name].find({"namespace": namespace})
        async for row in cursor:
            row.pop("_id", None)
            key = tuple(row.get(f) for f in VALUE_KEYS[entity_type])
            if all(k is not None and k != "" for k in key):
                index[key] = row
        return index
