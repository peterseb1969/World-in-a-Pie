"""The `migrate` surgery: re-pin a template's documents to another version,
filling what the target needs, offline.

Live ``migrate_documents`` refuses a document the target version would not
accept, for instance one missing a field the target makes mandatory. This
does the same migration on an archive, and fills such gaps from a mapping
(design doc §6.3 item 3).

**The mapping** (a dict; the toolkit reads it from a JSON file)::

    {"template": "MONSTER",          # id, value or ns:VALUE
     "to_version": 3,
     "from_versions": [1, 2],        # optional: default every other version
     "fill": {
        "region": "EU",                          # a constant
        "code":   {"copy": "legacy.code"},       # another field's value
        "label":  {"format": "{name} ({region})"},
        "meta":   {"value": {"copy": "x"}}       # a constant that looks like a rule
     }}

The target's declared renames apply first, then each fill in the order
given; a fill only sets a field that is absent or null, so it never
overwrites data. ``copy`` and ``format`` read the data as it stands after
the renames and the fills before them. Reference and file fields cannot be
filled: their snapshots cannot be resolved offline.

**Semantics are live migration's own.** The cohort is the template's
active documents on a source version; the target must be active, and its
``identity_fields`` must equal each source version's (otherwise it is a
fork, refused). Each migrated document is validated as a write against the
target, which re-resolves its terms (so a term deprecated since fails, as
live), and its identity hash must not change. Then, as live
``_create_new_version`` does:

- for a versioned template, the old row is retired (``status: inactive``,
  ``updated_at`` / ``updated_by``) and a new version row is written: the
  next version number, the target ``template_version``, the migrated data,
  the re-resolved term snapshots, the other snapshots carried with renamed
  paths, ``created_at`` carried, the validation's warnings in
  ``metadata.warnings``;
- for a ``versioned: false`` template, the row is overwritten in place.

The actor recorded is ``wip-toolkit:migrate``. A document the target would
refuse fails the plan unless ``skip_failing``, which leaves it on its old
version.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from typing import Any

from .definitions import UnresolvableTemplate
from .model import AmbiguousNameError, ArchiveModel
from .surgery import SurgeryError, actor, rename_paths, require_same_identity, stamp
from .validation import OfflineValidator

ACTOR = actor("migrate")
_MISSING = object()
_SAMPLE_CAP = 5


class MigrationRefused(SurgeryError):
    """The migration cannot be done as mapped; nothing is written."""

    def __init__(self, message: str, failures: list[str] | None = None) -> None:
        self.failures = failures or []
        super().__init__(message)


def _get(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _fill_value(rule: Any, data: dict[str, Any], name: str) -> Any:
    if isinstance(rule, dict) and len(rule) == 1:
        (kind, arg), = rule.items()
        if kind == "value":
            return arg
        if kind == "copy":
            value = _get(data, str(arg))
            return None if value is _MISSING else value
        if kind == "format":
            fields = {f for _t, f, _s, _c in string.Formatter().parse(str(arg)) if f}
            values = {f: _get(data, f) for f in fields}
            if any(v is _MISSING or v is None for v in values.values()):
                return None  # a source is empty: leave the field for validation to report
            return str(arg).format(**values)
    if isinstance(rule, dict) and set(rule) & {"copy", "format", "value"}:
        raise MigrationRefused(f"fill rule for {name!r} must have exactly one of copy, format, value")
    return rule


@dataclass
class MigrationPlan:
    """Which rows migrate, and the rows written in their place."""

    template_id: str
    template: str
    to_version: int
    mapping: dict[str, Any]
    target: dict[str, Any]
    versioned: bool
    cohort: set[tuple[str, int]] = field(default_factory=set)  # (document_id, version)
    next_version: dict[str, int] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)
    skipped: int = 0
    filled: dict[str, int] = field(default_factory=dict)  # field -> documents filled
    now: str = field(default_factory=stamp)
    _validator: OfflineValidator | None = None

    def keep_row(self, _ns: str, _entity_type: str, _row: dict[str, Any]) -> bool:
        return True

    def migrated_data(self, row: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """The row's data after renames and fills, and the fields filled."""
        data = dict(row.get("data") or {})
        for new_key, old_key in (self.target.get("renames") or {}).items():
            if old_key in data and new_key not in data:
                data[new_key] = data.pop(old_key)
        filled = []
        for name, rule in (self.mapping.get("fill") or {}).items():
            if data.get(name) is None:
                value = _fill_value(rule, data, name)
                if value is not None:
                    data[name] = value
                    filled.append(name)
        return data, filled

    def rewrite_row(self, ns: str, entity_type: str, row: dict[str, Any]) -> list[dict[str, Any]]:
        if entity_type != "documents" or (row.get("document_id"), row.get("version")) not in self.cohort:
            return [row]
        assert self._validator is not None
        data, _filled = self.migrated_data(row)
        outcome = self._validator.validate(self.target, data, str(row.get("namespace") or ns))
        renames = dict(self.target.get("renames") or {})
        references = rename_paths(row.get("references"), renames)
        require_same_identity(row, data, self.target, outcome.term_references, references)
        migrated = {
            **row,
            "template_version": self.to_version,
            "template_value": self.target.get("value", row.get("template_value")),
            "data": data,
            "term_references": outcome.term_references,
            "references": references,
            "file_references": rename_paths(row.get("file_references"), renames),
            "metadata": {**(row.get("metadata") or {}), "warnings": list(outcome.warnings)},
            "updated_at": self.now,
            "updated_by": ACTOR,
        }
        if not self.versioned:
            return [migrated]
        retired = {**row, "status": "inactive", "updated_at": self.now, "updated_by": ACTOR}
        migrated.update({
            "version": self.next_version[str(row["document_id"])],
            "status": "active",
            "created_by": ACTOR,
        })
        return [retired, migrated]

    def summary(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "to_version": self.to_version,
            "documents_migrated": len(self.cohort),
            "documents_skipped": self.skipped,
            "fields_filled": dict(self.filled),
        }


def plan_migration(reader: Any, model: ArchiveModel, mapping: dict[str, Any], *,
                   skip_failing: bool = False) -> MigrationPlan:
    """Validate the mapping and every document of the cohort; raise
    MigrationRefused when the migration cannot be done as mapped."""
    name = mapping.get("template")
    to_version = mapping.get("to_version")
    if not isinstance(name, str) or not isinstance(to_version, int):
        raise MigrationRefused("the mapping needs \"template\" (a name) and \"to_version\" (a number)")
    try:
        node = model.resolve_template(name)
    except AmbiguousNameError as e:
        raise MigrationRefused(str(e)) from e
    if node is None:
        raise MigrationRefused(f"no template {name!r} in this archive")
    definitions = model.definitions
    target_row = definitions.template(node.template_id, to_version)
    if target_row is None:
        raise MigrationRefused(f"{node.label} v{to_version} is not in the archive")
    if target_row.get("status") != "active":
        raise MigrationRefused(f"{node.label} v{to_version} is not active; a migration targets an active version")
    try:
        target = definitions.resolve_template(node.template_id, to_version).as_template_dict()
    except UnresolvableTemplate as e:
        raise MigrationRefused(f"{node.label} v{to_version} cannot be resolved: {e.reason}") from e

    fill_fields = {f["name"]: f for f in target.get("fields") or [] if f.get("name")}
    for fname in mapping.get("fill") or {}:
        ftype = (fill_fields.get(fname) or {}).get("type")
        if fname not in fill_fields:
            raise MigrationRefused(f"fill names {fname!r}, which {node.label} v{to_version} does not declare")
        if ftype in ("reference", "file"):
            raise MigrationRefused(f"fill cannot set {fname!r}: {ftype} fields cannot be resolved offline")

    sources = mapping.get("from_versions")
    validator = OfflineValidator(definitions)
    plan = MigrationPlan(
        template_id=node.template_id, template=f"{node.namespace}:{node.label}",
        to_version=to_version, mapping=mapping, target=target,
        versioned=bool(target_row.get("versioned", True)),
        _validator=validator,
    )
    identity = list(target.get("identity_fields") or [])
    checked_sources: dict[int, list[str]] = {}

    for ns in model.namespaces:
        for row in reader.read_entities("documents", namespace=ns):
            if row.get("template_id") != node.template_id or row.get("status") != "active":
                continue
            from_version = int(row.get("template_version") or 1)
            if from_version == to_version or (sources is not None and from_version not in sources):
                continue
            if from_version not in checked_sources:
                try:
                    checked_sources[from_version] = list(
                        definitions.resolve_template(node.template_id, from_version).identity_fields)
                except UnresolvableTemplate as e:
                    raise MigrationRefused(f"{node.label} v{from_version} cannot be resolved: {e.reason}") from e
                if checked_sources[from_version] != identity:
                    raise MigrationRefused(
                        f"identity_fields differ between v{from_version} and v{to_version}; "
                        "that is a fork, not a migration")
            data, filled = plan.migrated_data(row)
            outcome = validator.validate(target, data, str(row.get("namespace") or ns))
            problem = None
            if outcome.errors:
                first = outcome.errors[0]
                problem = f"{first.get('field')}: {first.get('code')}"
            else:
                try:
                    require_same_identity(row, data, target, outcome.term_references,
                                          rename_paths(row.get("references"),
                                                       dict(target.get("renames") or {})))
                except SurgeryError:
                    problem = "the identity hash would change"
            if problem is not None:
                if len(plan.failed) < _SAMPLE_CAP:
                    plan.failed.append(f"{row.get('document_id')} v{row.get('version')}: {problem}")
                plan.skipped += 1
                continue
            plan.cohort.add((str(row["document_id"]), int(row.get("version") or 1)))
            entry = model.documents.get(str(row["document_id"]))
            plan.next_version[str(row["document_id"])] = (entry.max_version if entry else 1) + 1
            for fname in filled:
                plan.filled[fname] = plan.filled.get(fname, 0) + 1

    if plan.skipped and not skip_failing:
        raise MigrationRefused(
            f"{plan.skipped} document(s) would not pass {plan.template} v{to_version}; "
            "nothing written (skip_failing leaves them on their version)", plan.failed)
    return plan
