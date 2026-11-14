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
``extend_terminologies`` — add terms the target's terminology is missing, and
the names a term has only in the archive.
``carry_retirements`` — retire the target's active terms the archive retired.

Without the first two, anything the target lacks is an incompatibility and the
merge refuses rather than quietly importing half a vocabulary. Without the
third, a term the archive retired stays active on the target and is reported
as kept, like any other field where the target's value wins.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Iterable
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
# recounts after it adds or retires terms. A term's terminology_value is
# def-store's copy of its terminology's value, filled in when the term is
# written: an archive older than the field lacks it, and the terminology
# itself is matched by value anyway.
_DERIVED_FIELDS = frozenset({
    "term_count", "terminology_value",
})

# Fields the services no longer have. An archive written before a field was
# retired still carries it, and a target row stored before the retirement
# may too; neither side's copy means anything now.
_RETIRED_FIELDS = frozenset({
    "extensible",
})

# A term's lifecycle. A difference is not a disagreement about what the term
# means, so it never refuses the merge. Retirement travels one way, and only
# under carry_retirements: then a term the archive retired (deprecated or
# inactive) while the target still has it active is retired on the target
# too, with the archive's reason and replacement. Without the flag it stays
# active and is counted (retirements_not_carried). A term the target already
# retired stays retired.
_TERM_LIFECYCLE_FIELDS = frozenset({
    "status", "deprecated_reason", "replaced_by_term_id",
})

# A template's or a terminology's lifecycle. Retiring a template version or a
# terminology does not change what it accepts, so a difference never refuses
# the merge: the target keeps its own status, and the difference is reported
# like a cosmetic one. Unlike a term's, this retirement does not travel —
# deactivating a target template or terminology is an operator's decision,
# never a merge side effect.
_DEFINITION_LIFECYCLE_FIELDS = frozenset({"status"})

# Vocabulary metadata. A difference here does not make two definitions
# incompatible — it is cosmetic, the target's wins, and it gets reported so an
# operator can see what their side kept. A term's names are compared as a
# set (see _name_key); under extend_terminologies the target gains the names
# only the archive has. unique_names is judged by its effect instead (see
# DefinitionsPlanner._check_unique_names): a difference refuses only where
# the names the merge adds would break the setting of the terminology the
# terms land in (the target's, an externally mapped one, or one it adds).
_COSMETIC_FIELDS = frozenset({
    "label", "description", "aliases", "names", "unique_names",
})

# Marks a field one side's row does not hold, in a refusal's detail: the
# comparison reads it as the model's default, and the report must not
# present that default as a value the side stored.
ABSENT = {"absent": True}


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
    # extend_terminologies: names only the archive's copy of a term has, to
    # add to the target's copy — {"term_id" (the target's), "names", "name"}.
    extend_names: list[dict[str, Any]] = field(default_factory=list)
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
                **({
                    "retire": len(self.retire_terms),
                    "extend_names": len(self.extend_names),
                } if entity_type == "terms" else {}),
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


def _names(term: dict[str, Any]) -> list[dict[str, Any]]:
    """A term's names, each a mapping. A row of an older shape may hold a
    bare string where a name belongs; it carries no type or language and is
    not read as a name here, by every reader alike."""
    return [n for n in term.get("names") or [] if isinstance(n, dict)]


def _name_key(name: dict[str, Any]) -> tuple[str, str, str]:
    """What makes a term's name the same name: its value, type and language.
    Its description, source and preferred flag are about the name, not which
    name it is."""
    return (
        str(name.get("value") or ""),
        str(name.get("type") or ""),
        str(name.get("language") or ""),
    )


def _name_keys(term: dict[str, Any]) -> set[tuple[str, str, str]]:
    """The _name_key of each of a term's names."""
    return {_name_key(n) for n in _names(term)}


def _comparable_term(row: dict[str, Any]) -> dict[str, Any]:
    """A term with its names in _name_key order, one per key. Each name is
    compared whole, so a difference in its description, source or preferred
    flag is a names difference too: cosmetic, and reported."""
    out = dict(row)
    by_key = {_name_key(n): n for n in _names(row)}
    out["names"] = [by_key[key] for key in sorted(by_key)]
    return out


def _term_texts(term: dict[str, Any]) -> list[str]:
    """Every string a term is resolved by: its value and its names' values."""
    texts = [str(term.get("value") or "")]
    texts += [str(n.get("value") or "") for n in _names(term)]
    return [t for t in dict.fromkeys(texts) if t]


def _versions(versions: set[int]) -> str:
    return ", ".join(f"v{v}" for v in sorted(versions)) or "none"


def _versions_by_value(rows: Iterable[dict[str, Any]]) -> dict[str, set[int]]:
    """The version numbers each template value has among these rows."""
    versions: dict[str, set[int]] = {}
    for row in rows:
        versions.setdefault(str(row.get("value")), set()).add(
            int(row.get("version") or 1)
        )
    return versions


def _no_common_version(
    value: str, archive_versions: set[int], target_versions: set[int]
) -> Incompatibility:
    """The refusal of a template value the target holds with no version in
    common with the archive."""
    return Incompatibility(
        entity_type="templates",
        name=value,
        reason=(
            "the target has this template, but no version of it is on "
            "both sides (archive "
            f"{_versions(archive_versions)}, target "
            f"{_versions(target_versions)}), so there is "
            "nothing to compare — merge with align_templates=true to map "
            "the archive's versions onto the target's by schema"
        ),
        detail={"versions": {
            "archive": sorted(archive_versions),
            "target": sorted(target_versions),
        }},
    )


def _lifecycle_fields(entity_type: str) -> tuple[frozenset[str], frozenset[str]]:
    """(term lifecycle fields, template/terminology lifecycle fields) that
    apply to this definition type; each empty where it does not apply."""
    lifecycle = _TERM_LIFECYCLE_FIELDS if entity_type == "terms" else frozenset()
    template_lifecycle = (
        _DEFINITION_LIFECYCLE_FIELDS
        if entity_type in ("templates", "terminologies") else frozenset()
    )
    return lifecycle, template_lifecycle


def _status_differs(
    fields: frozenset[str], archive: dict[str, Any], target: dict[str, Any]
) -> list[str]:
    """The fields (sorted) whose value differs, an absent one read as active."""
    return [
        f for f in sorted(fields)
        if (archive.get(f) or "active") != (target.get(f) or "active")
    ]


def _differing_content(
    entity_type: str,
    name: str,
    substantive: list[str],
    archive_row: dict[str, Any],
    target_row: dict[str, Any],
) -> Incompatibility:
    """The refusal of a definition both sides hold with different content."""
    return Incompatibility(
        entity_type=entity_type,
        name=name,
        reason=(
            "exists on both sides with different content "
            f"({', '.join(substantive)})"
        ),
        detail={
            field_name: {
                "target": target_row.get(field_name, ABSENT),
                "archive": archive_row.get(field_name, ABSENT),
            }
            for field_name in substantive
        },
    )


def _target_wins(
    entity_type: str,
    name: str,
    cosmetic: list[str],
    archive_row: dict[str, Any],
    target_row: dict[str, Any],
    added_names: list[dict[str, Any]] | None = None,
) -> TargetWins:
    """The report of the fields in which the target's value was kept.

    Where the merge adds the archive's names to the target's term
    (``added_names``), the archive's names are not discarded: the report
    names the target's names kept and the names added.
    """
    detail = {
        field_name: {
            "kept": target_row.get(field_name, ABSENT),
            "discarded": archive_row.get(field_name, ABSENT),
        }
        for field_name in cosmetic
    }
    if added_names and "names" in detail:
        detail["names"] = {
            "kept": target_row.get("names", ABSENT),
            "added": added_names,
        }
    return TargetWins(
        entity_type=entity_type, name=name, fields=cosmetic, detail=detail,
    )


def _names_to_add(
    missing: list[dict[str, Any]], target: dict[str, Any]
) -> list[dict[str, Any]]:
    """Copies of the names to add to the target's term, each losing
    ``preferred`` where a name of its type and language is already preferred
    (on the target, or earlier in this list), since a group has at most one."""
    preferred = {
        (n.get("type"), n.get("language"))
        for n in _names(target) if n.get("preferred")
    }
    names = []
    for n in missing:
        n = dict(n)
        group = (n.get("type"), n.get("language"))
        if n.get("preferred"):
            if group in preferred:
                n["preferred"] = False
            else:
                preferred.add(group)
        names.append(n)
    return names


def _target_names_kept(archive: dict[str, Any], target: dict[str, Any]) -> bool:
    """Whether the target's term holds a name the archive's copy does not
    hold as it is (another name, or the same name with another description,
    source or preferred flag): after a union, the target's copy of it was
    kept over the archive's."""
    archive_names = {_name_key(n): n for n in _names(archive)}
    return any(archive_names.get(_name_key(n)) != n for n in _names(target))


def _target_terminology(
    plan: DefinitionsPlan, external: dict[str, str], terminology_id: Any
) -> str:
    """The terminology id an archived term's terminology has on the target:
    through the plan's mapping, else the external mappings (a terminology
    the archive does not carry), else the archive's own id (a terminology
    this merge adds)."""
    key = str(terminology_id)
    return str(plan.mapping["terminologies"].get(key, external.get(key, key)))


def _is_active(row: dict[str, Any]) -> bool:
    return (row.get("status") or "active") == "active"


def _name_additions(
    plan: DefinitionsPlan, external: dict[str, str]
) -> dict[str, list[tuple[str, str]]]:
    """Per target terminology id, the (bearer, text) pairs this merge adds:
    every text of each active term it adds (bearer ``new:<value>``), and
    each name it adds to an existing term (bearer: that term's id). A term
    the archive retired bears no name def-store resolves, so it adds none."""
    additions: dict[str, list[tuple[str, str]]] = {}  # terminology -> (bearer, text)
    for row in plan.to_add["terms"]:
        if not _is_active(row):
            continue
        terminology = _target_terminology(plan, external, row.get("terminology_id"))
        bearer = f"new:{row.get('value')}"
        for text in _term_texts(row):
            additions.setdefault(terminology, []).append((bearer, text))
    for extension in plan.extend_names:
        for n in extension["names"]:
            additions.setdefault(str(extension["terminology_id"]), []).append(
                (str(extension["term_id"]), str(n.get("value")))
            )
    return additions


def _name_clashes(
    setting: dict[str, Any],
    existing: list[dict[str, Any]],
    added: list[tuple[str, str]],
) -> list[str]:
    """The names, as the terms store them, that the additions would give to
    more than one of the terminology's active terms (``existing``) or added
    terms. A name two target terms already share is the target's own state
    and does not refuse the merge.

    Names are matched as def-store matches them (lower-cased unless the
    terminology is case_sensitive) and reported as stored: every spelling
    of a clashing name, sorted. A name added to a term that is not among
    ``existing`` (retired, or retired by this merge) resolves nowhere and
    cannot clash.
    """
    fold = (lambda s: s) if setting.get("case_sensitive") else str.lower
    bearers: dict[str, set[str]] = {}
    spellings: dict[str, set[str]] = {}

    def bear(bearer: str, text: str) -> None:
        bearers.setdefault(fold(text), set()).add(bearer)
        spellings.setdefault(fold(text), set()).add(text)

    active = set()
    for row in existing:
        active.add(str(row.get("term_id")))
        for text in _term_texts(row):
            bear(str(row.get("term_id")), text)
    live = _live_additions(added, active)
    for bearer, text in live:
        bear(bearer, text)
    # Only a name the merge adds can clash: two target terms that already
    # share one are the target's own state, which the merge does not change.
    return _clashing_spellings(bearers, spellings, {fold(text) for _, text in live})


def _live_additions(
    added: list[tuple[str, str]], active: set[str]
) -> list[tuple[str, str]]:
    """The additions that land on a term that resolves: a new one, or an
    active term of the target's."""
    return [(b, t) for b, t in added if b.startswith("new:") or b in active]


def _clashing_spellings(
    bearers: dict[str, set[str]], spellings: dict[str, set[str]], touched: set[str],
) -> list[str]:
    """Every stored spelling of each folded name in ``touched`` that more
    than one term would bear, sorted."""
    shared = [f for f in touched if len(bearers.get(f, ())) > 1]
    return sorted(text for folded in shared for text in spellings[folded])


def _unique_names_broken(terminology_value: str, clashes: list[str]) -> Incompatibility:
    """The refusal of additions that would break a terminology's unique_names."""
    return Incompatibility(
        entity_type="terminologies",
        name=terminology_value,
        reason=(
            "the target's terminology has unique_names, and the terms "
            "and names this merge adds would give a name to more than "
            f"one term ({', '.join(clashes[:10])}"
            f"{' …' if len(clashes) > 10 else ''})"
        ),
        detail={"unique_names": {"name_collisions": clashes}},
    )


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
        if (key in _AUDIT_FIELDS or key in _ID_FIELDS or key in _DERIVED_FIELDS
                or key in _RETIRED_FIELDS or key in skip):
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
        # The target's template ids by value, for comparing template
        # references that name a template by its value.
        self._template_ids: dict[str, str] = {}
        self._namespace = ""

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
        self._namespace = namespace

        for entity_type in DEFINITION_TYPES:
            entities = entities_by_type.get(entity_type) or []
            if entities:
                await self._plan_type(entity_type, entities, namespace, plan)

        return plan

    async def _plan_type(
        self,
        entity_type: str,
        entities: list[dict[str, Any]],
        namespace: str,
        plan: DefinitionsPlan,
    ) -> None:
        """Check one definition type's archive entities against the target's."""
        if entity_type == "templates" and self._align_templates:
            entities = await self._align(entities, namespace, plan)
            if not entities:
                return
        target_index = await self._index_target(entity_type, namespace)
        if entity_type == "templates":
            entities = self._index_templates(entities, target_index, plan)
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
        if entity_type == "terms":
            await self._check_unique_names(plan)

    def _index_templates(
        self,
        entities: list[dict[str, Any]],
        target_index: dict[tuple[Any, ...], dict[str, Any]],
        plan: DefinitionsPlan,
    ) -> list[dict[str, Any]]:
        """Record the target's template ids by value; without align_templates,
        map the archive's templates onto them by value. Returns the archive
        templates left to compare per version."""
        self._template_ids = {
            str(row["value"]): str(row["template_id"])
            for row in target_index.values()
            if row.get("value") and row.get("template_id")
        }
        if self._align_templates:
            return entities
        return self._map_templates_by_value(entities, target_index, plan)

    def _map_templates_by_value(
        self,
        entities: list[dict[str, Any]],
        target_index: dict[tuple[Any, ...], dict[str, Any]],
        plan: DefinitionsPlan,
    ) -> list[dict[str, Any]]:
        """Record each archive template's id on the target by its value, and
        refuse the templates whose versions do not line up at all.

        A template's identity is its value: its template_id is the same in
        every version. So the archive's id maps onto the target's as soon as
        the target holds the value, whichever versions the two sides have —
        a reference to the template (an edge type's endpoints, a field's
        target_templates) names the same template either way. Matching the
        mapping per (value, version) instead leaves it unrecorded when no
        version number is on both sides, and every reference to that template
        then compares unequal.

        A value the target holds with no version in common with the archive
        is refused, once, naming align_templates: there is no version to
        compare, and adding the archive's versions under the target's id would
        splice two unrelated version histories into one template. Returns the
        archive templates left to compare per version.
        """
        target_versions = _versions_by_value(target_index.values())
        archive_versions = _versions_by_value(entities)
        kept: list[dict[str, Any]] = []
        refused: set[str] = set()
        for row in entities:
            value = str(row.get("value"))
            target_id = self._template_ids.get(value)
            if target_id is None:
                kept.append(row)
                continue
            archive_id = str(row.get("template_id") or "")
            if archive_id and archive_id != target_id:
                plan.mapping["templates"][archive_id] = target_id
            if archive_versions[value] & target_versions.get(value, set()):
                kept.append(row)
                continue
            if value in refused:
                continue
            refused.add(value)
            plan.incompatibilities.append(_no_common_version(
                value, archive_versions[value], target_versions.get(value, set())
            ))
        return kept

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
            remapped: dict[str, Any] = remapper.remap_template(entity)
            return remapped
        if entity_type == "terms":
            remapped = remapper.remap_term(entity)
            return remapped
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
        # side read as its model reads it (absent fields at their defaults),
        # and in the form that says what it means (see _comparable).
        archive_row = self._as_target_would_hold_it(entity_type, entity, remapper)
        target_row = target
        archive_filled = with_defaults(entity_type, archive_row)
        target_filled = with_defaults(entity_type, target_row)
        entity = self._comparable(entity_type, archive_filled)
        target = self._comparable(entity_type, target_filled)
        lifecycle, template_lifecycle = _lifecycle_fields(entity_type)
        cosmetic, substantive = compare_definitions(
            entity, target, skip=lifecycle | template_lifecycle,
        )
        if substantive:
            plan.incompatibilities.append(_differing_content(
                entity_type, name, substantive, archive_row, target_row
            ))
            return

        added_names: list[dict[str, Any]] = []
        if entity_type == "terms" and "names" in cosmetic:
            cosmetic, added_names = self._extend_names(
                archive_filled, target_filled, name, cosmetic, plan
            )

        cosmetic = cosmetic + _status_differs(template_lifecycle, entity, target)
        retired_here = False
        if lifecycle:
            kept = self._classify_term_lifecycle(entity, target, name, plan)
            cosmetic = cosmetic + kept
            retired_here = not kept and self._lifecycle_differs(entity, target)

        if cosmetic:
            plan.target_wins.append(_target_wins(
                entity_type, name, cosmetic, archive_row, target_row, added_names
            ))
        if retired_here:
            return  # retired on the target in pass 2, so not unchanged
        plan.unchanged[entity_type] += 1

    def _comparable(self, entity_type: str, row: dict[str, Any]) -> dict[str, Any]:
        """The row with each list whose order means nothing as a sorted set,
        and template references in the form that names the template.

        - A term's names: one per (value, type, language), see _name_key.
        - An edge type's source_templates / target_templates: the templates
          they name (_endpoint_key), whichever shape the row stores them in.
        - A field's target_templates: the same, sorted. An edge type's
          source_ref / target_ref constraint is derived from its endpoint
          declaration when the template is served, so a row that stores one
          (an archive written before it was derived) is read without it.
        """
        if entity_type == "terms":
            return _comparable_term(row)
        if entity_type != "templates":
            return row
        out = dict(row)
        for prop in ("source_templates", "target_templates"):
            out[prop] = sorted({self._endpoint_key(e) for e in row.get(prop) or []})
        edge = row.get("usage") == "relationship"
        out["fields"] = [
            self._comparable_field(f, edge) for f in row.get("fields") or []
        ]
        return out

    def _comparable_field(self, f: Any, edge: bool) -> Any:
        """A template field with its target_templates as _comparable reads
        them: dropped on an edge type's source_ref / target_ref, else the
        templates they name, sorted."""
        if not isinstance(f, dict):
            return f
        f = dict(f)
        if edge and f.get("name") in ("source_ref", "target_ref"):
            f["target_templates"] = None
        elif f.get("target_templates"):
            f["target_templates"] = sorted(
                {self._endpoint_key(t) for t in f["target_templates"]}
            )
        return f

    def _endpoint_key(self, entry: Any) -> str:
        """The template a reference names: its resolved id when it has one,
        else its value read as the target would resolve it (a bare value
        names the target namespace's template of that value)."""
        if isinstance(entry, dict):
            if entry.get("resolved"):
                return str(entry["resolved"])
            entry = entry.get("lookup_value")
        text = str(entry)
        own = f"{self._namespace}:"
        value = text[len(own):] if text.startswith(own) else text
        return self._template_ids.get(value, text)

    def _extend_names(
        self,
        archive: dict[str, Any],
        target: dict[str, Any],
        name: str,
        cosmetic: list[str],
        plan: DefinitionsPlan,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Names only the archive's copy of a term has: added to the target's
        copy under extend_terminologies, else the target's names stand and the
        difference is reported. Returns the cosmetic fields left to report and
        the names added.

        An added name loses ``preferred`` where the target already prefers a
        name of the same type and language, since a group has at most one.
        """
        held = _name_keys(target)
        missing = [n for n in _names(archive) if _name_key(n) not in held]
        if not missing or not self._extend_terminologies:
            return cosmetic, []
        added = _names_to_add(missing, target)
        plan.extend_names.append({
            "term_id": target.get(ID_KEYS["terms"]),
            "terminology_id": target.get("terminology_id"),
            "names": added,
            "name": name,
        })
        if not _target_names_kept(archive, target):
            return [f for f in cosmetic if f != "names"], added
        return cosmetic, added

    async def _check_unique_names(self, plan: DefinitionsPlan) -> None:
        """Refuse the terms that would break a terminology's unique_names.

        unique_names is judged by its effect, not compared, and the way
        def-store judges a term write: where the terminology holds it, no
        name or value may be borne by two active terms. Every term and name
        this merge adds is checked against the terminology's active terms,
        less those this merge retires, and against each other. The
        terminology is the one the terms land in: mapped by this plan, by
        the external mappings, or added by this merge with the archive's
        setting. Where it does not hold unique_names, nothing the merge adds
        can break it, and a difference is only reported.
        """
        external = self._external.terminology_map if self._external else {}
        additions = _name_additions(plan, external)
        if not additions:
            return
        settings = await self._terminology_settings(plan, list(additions))
        retiring = {str(r.get("term_id")) for r in plan.retire_terms}
        adding = {str(row.get("terminology_id")) for row in plan.to_add["terminologies"]}
        for terminology, added in additions.items():
            setting = settings.get(terminology)
            if not setting or not setting.get("unique_names"):
                continue
            # A terminology this merge adds has no terms here yet.
            existing = (
                [] if terminology in adding
                else await self._active_terms(setting, terminology, retiring)
            )
            clashes = _name_clashes(setting, existing, added)
            if clashes:
                plan.incompatibilities.append(
                    _unique_names_broken(str(setting.get("value")), clashes)
                )

    async def _terminology_settings(
        self, plan: DefinitionsPlan, terminology_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """The unique_names / case_sensitive settings of these terminologies,
        by id: the archive's for one this merge adds, else the target's. The
        target's are read by id alone: an externally mapped terminology can
        live outside the namespace merged into."""
        settings = {
            str(row.get("terminology_id")): row
            for row in plan.to_add["terminologies"]
        }
        stored = [t for t in terminology_ids if t not in settings]
        if not stored:
            return settings
        db_name, coll_name = self._collection_map["terminologies"]
        async for row in self._mongo[db_name][coll_name].find(
            {"terminology_id": {"$in": stored}},
            {"terminology_id": 1, "namespace": 1, "value": 1, "unique_names": 1,
             "case_sensitive": 1},
        ):
            settings[str(row["terminology_id"])] = row
        return settings

    async def _active_terms(
        self, setting: dict[str, Any], terminology_id: str, retiring: set[str]
    ) -> list[dict[str, Any]]:
        """The target's active terms of one stored terminology (value, names),
        less those this merge retires. Read in the terminology's own
        namespace, which every terms index leads with: an externally mapped
        terminology can live outside the namespace merged into."""
        db_name, coll_name = self._collection_map["terms"]
        return [
            row async for row in self._mongo[db_name][coll_name].find(
                {"namespace": setting.get("namespace"), "terminology_id": terminology_id},
                {"term_id": 1, "value": 1, "names": 1, "status": 1},
            )
            if _is_active(row) and str(row.get("term_id")) not in retiring
        ]

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
        are walked in dependency order — and, for a terminology the archive
        does not carry, through the external mappings, as a comparison of
        the term's references would be.
        """
        external = self._external.terminology_map if self._external else {}
        values: list[Any] = []
        for field_name in VALUE_KEYS[entity_type]:
            value = entity.get(field_name)
            if value is None or value == "":
                return None
            if field_name == "terminology_id":
                value = _target_terminology(plan, external, value)
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
# A client's prefill, which the platform never applies: it does not change
# which documents a field accepts, so it does not keep two versions apart.
_FIELD_INFORMATIONAL_KEYS = frozenset({"default_value"})


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
    """The template's fields by name, as a document is validated against them
    (so without what validation never reads: resolution markers, default_value).

    An edge type's source_ref/target_ref constraint is derived from its
    endpoint declaration when the template is served, so what a stored field
    carries there is not schema.
    """
    edge = template.get("usage") == "relationship"
    fields: dict[str, dict[str, Any]] = {}
    for f in template.get("fields") or []:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        f = {
            k: v for k, v in f.items()
            if k not in _FIELD_RESOLUTION_KEYS and k not in _FIELD_INFORMATIONAL_KEYS
        }
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
        if t_fields[name].get("mandatory")
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
