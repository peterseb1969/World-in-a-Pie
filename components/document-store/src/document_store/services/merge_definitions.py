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
active decision, never a side effect of restoring data into it, so the three
strategies that would change them are opt-in:

``add_missing`` — insert terminologies and templates the target does not have.
``extend_terminologies`` — add terms the target's terminology is missing.
``carry_retirements`` — retire the target's active terms the archive retired.

Without the first two, anything the target lacks is an incompatibility and the
merge refuses rather than quietly importing half a vocabulary. Without the
third, a term the archive retired stays active on the target and is reported
as kept, like any other field where the target's value wins.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

from wip_archive.remap import IDRemapper

from ._definition_defaults import DEFINITION_DEFAULTS

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

# Computed from other entities, not content of the definition: a
# terminology's term_count is def-store's count of its active terms, and the
# terms are compared one by one. Two copies that differ only here hold
# different terms, which the term comparison already decides; the merge
# recounts after it adds or retires terms.
_DERIVED_FIELDS = frozenset({
    "term_count",
})

# A term's lifecycle. A difference is not a disagreement about what the term
# means, so it never refuses the merge. Retirement travels one way: a term
# the archive retired (deprecated or inactive) while the target still has it
# active is retired on the target too, with the archive's reason and
# replacement; a term the target already retired stays retired.
_TERM_LIFECYCLE_FIELDS = frozenset({
    "status", "deprecated_reason", "replaced_by_term_id",
})

# A template's lifecycle. Retiring a version does not change what its schema
# accepts, so a difference never refuses the merge: the target keeps its own
# status, and the difference is reported like a cosmetic one. Unlike a term's,
# a template's retirement does not travel — deactivating a target template is
# an operator's decision, never a merge side effect.
_TEMPLATE_LIFECYCLE_FIELDS = frozenset({"status"})

# Vocabulary metadata. A difference here does not make two definitions
# incompatible — it is cosmetic, the target's wins, and it gets reported so an
# operator can see what their side kept.
_COSMETIC_FIELDS = frozenset({
    "label", "description", "aliases",
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
class VersionAlignment:
    """Where one archive template version lands under ``align_templates``.

    ``target_version`` is None when no target version can take its documents.
    ``removed_fields`` are fields the archive version has and the target
    version lacks: the mapping holds only if they are empty in every arriving
    document pinned to this version, which the engine checks once it has seen
    the documents.
    """

    archive_version: int
    target_version: int | None
    reason: str
    removed_fields: list[str] = field(default_factory=list)


@dataclass
class TemplateAlignment:
    """An archive template matched to the target's template of the same value,
    and each of its versions mapped onto a target version."""

    value: str
    archive_template_id: str
    target_template_id: str
    versions: dict[int, VersionAlignment] = field(default_factory=dict)

    def version_map(self) -> dict[int, int]:
        return {
            v: a.target_version
            for v, a in self.versions.items()
            if a.target_version is not None
        }


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
    # Terms the archive retired that the target still has active: the
    # target's term_id plus the archive's lifecycle values. replaced_by_term_id
    # is still the archive's id here; pass 2 maps it to the target's.
    retire_terms: list[dict[str, Any]] = field(default_factory=list)
    # Terms the archive retired that stay active on the target because
    # carry_retirements is off: their names, for the plan report.
    retirements_not_carried: list[str] = field(default_factory=list)
    # Definitions present and identical on both sides — nothing to do.
    unchanged: dict[str, int] = field(
        default_factory=lambda: {t: 0 for t in DEFINITION_TYPES}
    )
    # align_templates: the archive templates matched by value, keyed by the
    # archive's template_id.
    alignment: dict[str, TemplateAlignment] = field(default_factory=dict)

    @property
    def compatible(self) -> bool:
        return not self.incompatibilities

    def summary(self) -> dict[str, dict[str, int]]:
        return {
            entity_type: {
                "unchanged": self.unchanged[entity_type],
                "add": len(self.to_add[entity_type]),
                "mapped": len(self.mapping[entity_type]),
                **({"retire": len(self.retire_terms)} if entity_type == "terms" else {}),
            }
            for entity_type in DEFINITION_TYPES
        }


def with_defaults(entity_type: str, row: dict[str, Any]) -> dict[str, Any]:
    """The row as its model reads it: every absent field at its default.

    A row written before a field existed lacks the key, and def-store or
    template-store reads it as the field's default; the comparison must read
    it the same way, or an archive one release older than the target differs
    from it in every field added since. Nested models (a template's fields,
    rules and reporting, a term's names, a terminology's metadata) are
    filled too. Only absent keys are filled — a value either side holds is
    never changed — and the row itself is not modified.
    """
    root = DEFINITION_DEFAULTS["roots"].get(entity_type)
    return _fill(root, row) if root else row


def _fill(model: str, value: Any) -> Any:
    spec = DEFINITION_DEFAULTS["models"][model]
    if not isinstance(value, dict):
        return value
    filled = dict(value)
    for name, default in spec["defaults"].items():
        if name not in filled:
            filled[name] = copy.deepcopy(default)
    for name, (nested, many) in spec["nested"].items():
        held = filled.get(name)
        if many and isinstance(held, list):
            filled[name] = [_fill(nested, item) for item in held]
        elif not many and isinstance(held, dict):
            filled[name] = _fill(nested, held)
    return filled


def compare_definitions(
    archive: dict[str, Any],
    target: dict[str, Any],
    *,
    skip: frozenset[str] = frozenset(),
) -> tuple[list[str], list[str]]:
    """Split a definition's differences into cosmetic and substantive.

    Returns ``(cosmetic, substantive)`` field-name lists. Audit fields,
    canonical IDs and derived fields are excluded from both: two honest copies
    of one definition differ in ``updated_at``, (across installs) in their
    UUIDs, and in counts computed from other entities, and none is a finding.
    ``skip`` excludes further fields the caller compares on its own.
    """
    cosmetic: list[str] = []
    substantive: list[str] = []
    for key in sorted(set(archive) | set(target)):
        if key in _AUDIT_FIELDS or key in _ID_FIELDS or key in _DERIVED_FIELDS or key in skip:
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
        align_templates: bool = False,
        carry_retirements: bool = False,
    ):
        self._mongo = mongo_client
        self._collection_map = collection_map
        self._add_missing = add_missing
        self._extend_terminologies = extend_terminologies
        self._align_templates = align_templates
        self._carry_retirements = carry_retirements
        self._external: IDRemapper | None = None

    async def plan(
        self,
        entities_by_type: dict[str, list[dict[str, Any]]],
        namespace: str,
        *,
        external: IDRemapper | None = None,
    ) -> DefinitionsPlan:
        """Check the archive's definitions against the target's.

        ``external`` holds the mappings for referenced entities the archive
        does not carry (a terminology in another namespace, say), so a
        reference to one compares by what it names on this install.
        """
        plan = DefinitionsPlan()
        self._external = external

        for entity_type in DEFINITION_TYPES:
            entities = entities_by_type.get(entity_type) or []
            if not entities:
                continue
            if entity_type == "templates" and self._align_templates:
                entities = await self._align(entities, namespace, plan)
                if not entities:
                    continue
            target_index = await self._index_target(entity_type, namespace)
            # Match the whole type before comparing any of it: a definition
            # can reference another of its own type (a template's extends or
            # template_ref, a term's parent_term_id), and that reference
            # compares equal only once its own mapping is known.
            matched = [
                (entity, self._match(entity_type, entity, target_index, plan))
                for entity in entities
            ]
            remapper = self._remapper(plan)
            for entity, target in matched:
                self._classify(entity_type, entity, target, remapper, plan)

        return plan

    def _match(
        self,
        entity_type: str,
        entity: dict[str, Any],
        target_index: dict[tuple[Any, ...], dict[str, Any]],
        plan: DefinitionsPlan,
    ) -> dict[str, Any] | None:
        """The target's copy of this definition, recording the id mapping."""
        key = self._natural_key(entity_type, entity, plan)
        target = target_index.get(key) if key is not None else None
        if target is not None:
            old_id = entity.get(ID_KEYS[entity_type])
            new_id = target.get(ID_KEYS[entity_type])
            if old_id and new_id and old_id != new_id:
                plan.mapping[entity_type][str(old_id)] = str(new_id)
        return target

    def _remapper(self, plan: DefinitionsPlan) -> IDRemapper:
        """The plan's mappings so far (over any external ones), as the
        remapper the data pass uses."""
        remapper = IDRemapper()
        external = self._external
        if external is not None:
            remapper.terminology_map.update(external.terminology_map)
            remapper.term_map.update(external.term_map)
            remapper.template_map.update(external.template_map)
        remapper.terminology_map.update(plan.mapping["terminologies"])
        remapper.term_map.update(plan.mapping["terms"])
        remapper.template_map.update(plan.mapping["templates"])
        return remapper

    @staticmethod
    def _as_target_would_hold_it(
        entity_type: str, entity: dict[str, Any], remapper: IDRemapper
    ) -> dict[str, Any]:
        """The archive's definition with every reference it holds rewritten
        through the plan's mappings, so ids that differ only because the two
        sides minted them separately compare equal."""
        if entity_type == "templates":
            return remapper.remap_template(entity)
        if entity_type == "terms":
            return remapper.remap_term(entity)
        return entity

    def _classify(
        self,
        entity_type: str,
        entity: dict[str, Any],
        target: dict[str, Any] | None,
        remapper: IDRemapper,
        plan: DefinitionsPlan,
    ) -> None:
        name = self._describe(entity_type, entity)

        if target is None:
            self._classify_missing(entity_type, entity, name, plan)
            return

        # Present on both sides: compare content, references mapped, each
        # side read as its model reads it (absent fields at their defaults).
        entity = with_defaults(
            entity_type, self._as_target_would_hold_it(entity_type, entity, remapper)
        )
        target = with_defaults(entity_type, target)
        lifecycle = _TERM_LIFECYCLE_FIELDS if entity_type == "terms" else frozenset()
        template_lifecycle = (
            _TEMPLATE_LIFECYCLE_FIELDS if entity_type == "templates" else frozenset()
        )
        cosmetic, substantive = compare_definitions(
            entity, target, skip=lifecycle | template_lifecycle,
        )
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

        cosmetic = cosmetic + [
            f for f in sorted(template_lifecycle)
            if (entity.get(f) or "active") != (target.get(f) or "active")
        ]
        retired_here = False
        if lifecycle:
            kept = self._classify_term_lifecycle(entity, target, name, plan)
            cosmetic = cosmetic + kept
            retired_here = not kept and self._lifecycle_differs(entity, target)

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
        if retired_here:
            return  # retired on the target in pass 2, so not unchanged
        plan.unchanged[entity_type] += 1

    @staticmethod
    def _lifecycle_differs(archive: dict[str, Any], target: dict[str, Any]) -> bool:
        return any(archive.get(f) != target.get(f) for f in _TERM_LIFECYCLE_FIELDS)

    def _classify_term_lifecycle(
        self,
        archive: dict[str, Any],
        target: dict[str, Any],
        name: str,
        plan: DefinitionsPlan,
    ) -> list[str]:
        """Carry the archive's retirement of a term, or keep the target's.

        Returns the lifecycle fields in which the target's value was kept, for
        the target-wins report (empty when nothing differs or the archive's
        retirement is carried over). A retirement is carried only under
        carry_retirements: deprecating a live term changes the target's
        definitions, which a merge does only when asked to.
        """
        if not self._lifecycle_differs(archive, target):
            return []
        archive_status = archive.get("status") or "active"
        target_status = target.get("status") or "active"
        retired_in_archive = archive_status != "active" and target_status == "active"
        if retired_in_archive and not self._carry_retirements:
            plan.retirements_not_carried.append(name)
        elif retired_in_archive:
            plan.retire_terms.append({
                "term_id": target.get(ID_KEYS["terms"]),
                "terminology_id": target.get("terminology_id"),
                "status": archive_status,
                "deprecated_reason": archive.get("deprecated_reason"),
                "replaced_by_term_id": archive.get("replaced_by_term_id"),
                "name": name,
            })
            return []
        # The target retired it, or both retired it differently: a merge never
        # brings a retired term back, and the target's retirement stands. Or
        # the archive retired it and carry_retirements is off: the target's
        # active term stands.
        return sorted(
            f for f in _TERM_LIFECYCLE_FIELDS if archive.get(f) != target.get(f)
        )

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

    async def _align(
        self,
        archive_templates: list[dict[str, Any]],
        namespace: str,
        plan: DefinitionsPlan,
    ) -> list[dict[str, Any]]:
        """align_templates: match templates by value, map versions by schema.

        Returns the archive templates whose value the target does not hold;
        those go through the per-version path as before (refused, or added
        under add_missing). Everything else is recorded in ``plan.alignment``
        or refused into ``plan.incompatibilities`` here.
        """
        db_name, coll_name = self._collection_map["templates"]
        target_by_value: dict[str, list[dict[str, Any]]] = {}
        async for row in self._mongo[db_name][coll_name].find({"namespace": namespace}):
            row.pop("_id", None)
            if row.get("value"):
                target_by_value.setdefault(row["value"], []).append(row)
        archive_by_value: dict[str, list[dict[str, Any]]] = {}
        for row in archive_templates:
            archive_by_value.setdefault(str(row.get("value")), []).append(row)

        unmatched: list[dict[str, Any]] = []
        aligned: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
        for value, rows in archive_by_value.items():
            targets = target_by_value.get(value)
            if not targets:
                unmatched.extend(rows)
                continue
            archive_id = str(rows[0].get("template_id"))
            target_id = str(targets[0].get("template_id"))
            refusal = self._alignment_refusal(value, archive_id, target_id, rows, targets)
            if refusal:
                plan.incompatibilities.append(refusal)
                continue
            plan.mapping["templates"][archive_id] = target_id
            plan.alignment[archive_id] = TemplateAlignment(
                value=value, archive_template_id=archive_id, target_template_id=target_id,
            )
            aligned[archive_id] = (rows, targets)

        # Version references between templates (extends_version, a field's
        # template_ref_version) compare through the referenced template's
        # alignment, so the mapping is refined until it stops changing.
        remapper = self._remapper(plan)
        for _ in range(len(aligned) + 1):
            changed = False
            for archive_id, (rows, targets) in aligned.items():
                alignment = plan.alignment[archive_id]
                for row in rows:
                    version = int(row.get("version") or 1)
                    result = self._align_version(row, targets, remapper, plan)
                    if alignment.versions.get(version) != result:
                        alignment.versions[version] = result
                        changed = True
            if not changed:
                break
        for alignment in plan.alignment.values():
            plan.unchanged["templates"] += sum(
                1 for a in alignment.versions.values() if a.target_version is not None
            )
        return unmatched

    @staticmethod
    def _alignment_refusal(
        value: str,
        archive_id: str,
        target_id: str,
        rows: list[dict[str, Any]],
        targets: list[dict[str, Any]],
    ) -> Incompatibility | None:
        """Why this template cannot be aligned at all, or None."""
        if archive_id == target_id:
            return Incompatibility(
                entity_type="templates",
                name=value,
                reason=(
                    "it is the same template on both sides (one install minted "
                    "it), so its versions already line up — merge without "
                    "align_templates"
                ),
            )
        archive_latest = max(rows, key=lambda r: int(r.get("version") or 1))
        target_latest = max(targets, key=lambda r: int(r.get("version") or 1))
        archive_latest = with_defaults("templates", archive_latest)
        target_latest = with_defaults("templates", target_latest)
        for key in ("identity_fields", "usage", "versioned"):
            if archive_latest.get(key) != target_latest.get(key):
                return Incompatibility(
                    entity_type="templates",
                    name=value,
                    reason=(
                        f"its {key} differs between the two sides — a different "
                        "template, not a version of this one"
                    ),
                    detail={key: {
                        "target": target_latest.get(key),
                        "archive": archive_latest.get(key),
                    }},
                )
        return None

    def _align_version(
        self,
        row: dict[str, Any],
        targets: list[dict[str, Any]],
        remapper: IDRemapper,
        plan: DefinitionsPlan,
    ) -> VersionAlignment:
        """The target version one archive version maps onto, by schema."""
        version = int(row.get("version") or 1)
        archive = self._with_aligned_version_refs(
            with_defaults("templates", remapper.remap_template(row)), plan
        )
        archive_schema = _schema(archive)
        filled = sorted(
            (with_defaults("templates", t) for t in targets),
            key=lambda t: (t.get("status") == "active", int(t.get("version") or 1)),
        )
        for target in reversed(filled):
            if _schema(target) == archive_schema:
                return VersionAlignment(
                    archive_version=version,
                    target_version=int(target.get("version") or 1),
                    reason=f"same schema as target v{target.get('version')}",
                )
        latest = filled[-1]
        target_version = int(latest.get("version") or 1)
        problem, removed = _compatibility(archive, latest)
        if problem:
            return VersionAlignment(
                archive_version=version,
                target_version=None,
                reason=f"not compatible with target v{target_version}: {problem}",
            )
        return VersionAlignment(
            archive_version=version,
            target_version=target_version,
            reason=f"compatible with target v{target_version}",
            removed_fields=removed,
        )

    @staticmethod
    def _with_aligned_version_refs(
        template: dict[str, Any], plan: DefinitionsPlan
    ) -> dict[str, Any]:
        """Version numbers this template holds for other templates, in the
        target's numbering where those templates are aligned."""
        by_target: dict[str, dict[int, int]] = {
            a.target_template_id: a.version_map() for a in plan.alignment.values()
        }

        def mapped(template_id: Any, version: Any) -> Any:
            versions = by_target.get(str(template_id))
            if versions is None or version is None:
                return version
            return versions.get(int(version), version)

        result = dict(template)
        if result.get("extends"):
            result["extends_version"] = mapped(result["extends"], result.get("extends_version"))
        fields = []
        for f in result.get("fields") or []:
            f = dict(f)
            if f.get("template_ref"):
                f["template_ref_version"] = mapped(f["template_ref"], f.get("template_ref_version"))
            if f.get("array_template_ref"):
                f["array_template_ref_version"] = mapped(
                    f["array_template_ref"], f.get("array_template_ref_version")
                )
            fields.append(f)
        result["fields"] = fields
        return result

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


# What a document is validated against: two versions with the same schema can
# take the same documents. Reporting, headers, labels and audit fields are not
# part of it.
_SCHEMA_KEYS = (
    "identity_fields", "rules", "usage", "versioned",
    "source_templates", "target_templates", "extends", "extends_version",
)
# Filled in when a template is resolved, never part of the stored schema.
_FIELD_RESOLUTION_KEYS = frozenset({"inherited", "inherited_from"})


def _endpoints(entries: Any) -> list[str]:
    """An edge type's declared endpoints by what they name."""
    out = []
    for entry in entries or []:
        if isinstance(entry, dict):
            out.append(str(entry.get("resolved") or entry.get("lookup_value")))
        else:
            out.append(str(entry))
    return sorted(out)


def _fields(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The template's fields by name, as a document is validated against them.

    An edge type's source_ref/target_ref constraint is derived from its
    endpoint declaration when the template is served, so what a stored field
    carries there is not schema.
    """
    edge = template.get("usage") == "relationship"
    fields: dict[str, dict[str, Any]] = {}
    for f in template.get("fields") or []:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        f = {k: v for k, v in f.items() if k not in _FIELD_RESOLUTION_KEYS}
        if edge and f["name"] in ("source_ref", "target_ref"):
            f.pop("target_templates", None)
        fields[f["name"]] = f
    return fields


def _schema(template: dict[str, Any]) -> dict[str, Any]:
    schema = {key: template.get(key) for key in _SCHEMA_KEYS}
    schema["source_templates"] = _endpoints(template.get("source_templates"))
    schema["target_templates"] = _endpoints(template.get("target_templates"))
    schema["fields"] = _fields(template)
    return schema


def _compatibility(
    archive: dict[str, Any], target: dict[str, Any]
) -> tuple[str | None, list[str]]:
    """Can documents of the archive version be pinned to the target version?

    The migration-eligibility rule template create reports: added optional
    fields are fine; a removed field is fine only where the arriving
    documents leave it empty (returned, for the engine to check); a type
    change, a newly required field, a modified field, and any difference in
    identity, rules, usage, versioning or endpoints are not. Returns
    ``(problem, removed_fields)``; ``problem`` is None when compatible.
    """
    a_schema, t_schema = _schema(archive), _schema(target)
    for key in _SCHEMA_KEYS:
        if a_schema[key] != t_schema[key]:
            return f"{key} differs", []
    a_fields, t_fields = a_schema["fields"], t_schema["fields"]
    added_required = sorted(
        name for name in set(t_fields) - set(a_fields)
        if t_fields[name].get("mandatory") and t_fields[name].get("default_value") is None
    )
    if added_required:
        return f"the target requires new field(s) {', '.join(added_required)}", []
    for name in sorted(set(a_fields) & set(t_fields)):
        old, new = a_fields[name], t_fields[name]
        if old.get("type") != new.get("type"):
            return f"field '{name}' changed type", []
        if not old.get("mandatory") and new.get("mandatory"):
            return f"field '{name}' became required", []
        if old != new:
            return f"field '{name}' changed", []
    return None, sorted(set(a_fields) - set(t_fields))
