"""Would a template's documents migrate to another version? Asked of an archive.

The offline counterpart of `POST /documents/migrate` with `dry_run`
(document-store `migrate_document_version` / `_migrate_one`), so a migration
can be assessed on any archive without an instance. It follows the live rules:

- the cohort is the ACTIVE document rows of the template, grouped by the
  version they are pinned to (live migrates one source version at a time);
- the whole migration is refused when the target version is missing or not
  active, or when its `identity_fields` differ from the source's (a fork, not
  a migration);
- the target's declared `renames` (new_field -> old_field) re-key the data
  first;
- each document is validated as a WRITE against the target: terms ARE
  re-resolved, so a term deprecated since the document was written fails it,
  exactly as live migration refuses it;
- a recomputed identity hash that differs from the stored one fails the
  document (`identity_fields_changed`).

Not reproduced: reference and file checks, which live re-runs on every write
and an archive cannot (their targets may lie outside it). A `ready` document is
ready as far as the offline validator can tell; the report says so.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from wip_archive.archive import ArchiveReader
from wip_archive.definitions import DefinitionIndex, UnresolvableTemplate
from wip_archive.identity import compute_composite_key_hash, resolved_identity_values
from wip_archive.validation import OfflineValidator

MAX_SAMPLES = 5


@dataclass
class CohortReport:
    """The documents pinned to one source version."""

    from_version: int
    documents: int = 0
    ready: int = 0
    # Refusal of the whole cohort, as live refuses the operation.
    blocked: str | None = None
    # (field, code) -> documents failing with it
    failures: Counter = field(default_factory=Counter)
    samples: list[str] = field(default_factory=list)
    identity_changed: int = 0
    unverifiable: Counter = field(default_factory=Counter)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_version": self.from_version,
            "documents": self.documents,
            "ready": self.ready,
            "failing": self.documents - self.ready if not self.blocked else self.documents,
            "blocked": self.blocked,
            "failures": [
                {"field": f, "code": c, "documents": n}
                for (f, c), n in sorted(self.failures.items(), key=lambda kv: (-kv[1], str(kv[0])))
            ],
            "identity_changed": self.identity_changed,
            "samples": list(self.samples),
            "unverifiable": [
                {"kind": k, "ref": r, "documents": n}
                for (k, r), n in sorted(self.unverifiable.items(), key=str)
            ],
        }


@dataclass
class MigrationReport:
    template_id: str
    template: str
    to_version: int
    blocked: str | None = None
    cohorts: dict[int, CohortReport] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "template": self.template,
            "to_version": self.to_version,
            "blocked": self.blocked,
            "references_checked": False,
            "cohorts": [self.cohorts[v].to_dict() for v in sorted(self.cohorts)],
        }


def assess_migration(
    reader: ArchiveReader,
    definitions: DefinitionIndex,
    template_id: str,
    to_version: int,
    namespaces: list[str] | None = None,
) -> MigrationReport:
    """Assess migrating every active document of `template_id` to `to_version`."""
    target_row = definitions.template(template_id, to_version)
    label = str((target_row or {}).get("value") or template_id)
    report = MigrationReport(template_id=template_id, template=label, to_version=to_version)

    if target_row is None:
        report.blocked = f"version {to_version} is not in the archive"
        return report
    if target_row.get("status") != "active":
        report.blocked = (
            f"version {to_version} is not active; migration must target an active version"
        )
        return report
    try:
        target = definitions.resolve_template(template_id, to_version).as_template_dict()
    except UnresolvableTemplate as e:
        report.blocked = f"version {to_version} cannot be resolved from the archive: {e.reason}"
        return report

    renames = dict(target.get("renames") or {})
    target_identity = list(target.get("identity_fields") or [])
    validator = OfflineValidator(definitions)
    sources: dict[int, list[str] | UnresolvableTemplate] = {}

    for ns in namespaces if namespaces is not None else reader.list_namespaces():
        for row in reader.read_entities("documents", namespace=ns):
            if row.get("template_id") != template_id or row.get("status") != "active":
                continue
            from_version = int(row.get("template_version") or 1)
            if from_version == to_version:
                continue
            cohort = report.cohorts.setdefault(from_version, CohortReport(from_version))
            cohort.documents += 1

            if from_version not in sources:
                try:
                    source = definitions.resolve_template(template_id, from_version)
                    sources[from_version] = list(source.identity_fields)
                except UnresolvableTemplate as e:
                    sources[from_version] = e
                source_identity = sources[from_version]
                if isinstance(source_identity, UnresolvableTemplate):
                    cohort.blocked = f"version {from_version} is not in the archive"
                elif source_identity != target_identity:
                    cohort.blocked = (
                        f"identity_fields differ between v{from_version} ({source_identity}) "
                        f"and v{to_version} ({target_identity}); this is a fork, not a migrate"
                    )
            if cohort.blocked:
                continue

            _assess_document(row, target, renames, validator, cohort, ns)
    return report


def _assess_document(
    row: dict[str, Any], target: dict[str, Any], renames: dict[str, str],
    validator: OfflineValidator, cohort: CohortReport, ns: str,
) -> None:
    tag = f"{row.get('document_id')} v{row.get('version', 1)}"
    data = dict(row.get("data") or {})
    for new_key, old_key in renames.items():
        if old_key in data and new_key not in data:
            data[new_key] = data.pop(old_key)

    outcome = validator.validate(target, data, str(row.get("namespace") or ns))
    for entry in outcome.unverifiable:
        cohort.unverifiable[(entry["kind"], entry["ref"])] += 1
    if outcome.errors:
        for error in outcome.errors:
            cohort.failures[(error.get("field"), error.get("code"))] += 1
        if len(cohort.samples) < MAX_SAMPLES:
            first = outcome.errors[0]
            cohort.samples.append(f"{tag}: {first.get('field')} {first.get('code')}")
        return

    identity_fields = target.get("identity_fields") or []
    stored = row.get("identity_hash")
    if identity_fields and stored:
        try:
            recomputed: str | None = compute_composite_key_hash(resolved_identity_values(
                data, list(identity_fields), list(target.get("fields") or []),
                outcome.term_references, row.get("references"),
            ))
        except (ValueError, KeyError, TypeError):
            recomputed = None
        if recomputed != stored:
            cohort.identity_changed += 1
            cohort.failures[(None, "identity_fields_changed")] += 1
            if len(cohort.samples) < MAX_SAMPLES:
                cohort.samples.append(f"{tag}: identity hash would change")
            return
    cohort.ready += 1
