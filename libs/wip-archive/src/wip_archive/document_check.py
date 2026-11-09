"""Check every document of an archive against the definitions it carries.

The pass behind `verify` (CASE-827 M2). For each document row, against the
template version it is pinned to:

- the template version must be there to judge against;
- its data must pass the offline validator's structural, field and rule
  stages — not the term stage, because a stored document must not be
  re-resolved as a new write (existing data keeps resolving retired terms,
  PoNIF #1);
- its stored `term_references`, and the template and terminology targets of
  its `references`, must point at entities the archive carries (in any of
  its namespaces) or records as referenced-but-not-carried — checked even
  when the document's own template cannot be judged;
- its `identity_hash` must equal the hash recomputed offline from its data.

It streams: one document at a time, counters keyed by what failed and capped
samples only, so it adds nothing per document to the model's memory.

What the archive cannot judge — a template, parent or vocabulary it does not
carry — is collected as an unverifiable dependency, never counted as a pass
or a failure.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from wip_archive.definitions import DefinitionIndex, UnresolvableTemplate
from wip_archive.identity import compute_composite_key_hash, resolved_identity_values
from wip_archive.validation import OfflineValidator

MAX_SAMPLES = 5


def _sample(samples: list[str], text: str) -> None:
    if len(samples) < MAX_SAMPLES:
        samples.append(text)


@dataclass
class DocumentCheck:
    definitions: DefinitionIndex
    # True when the archive carries a referents table, so a term id absent
    # from both the archive and the table is provably missing.
    has_referents_table: bool
    # Ids the archive carries in namespaces outside the checked selection
    # (verify --namespace), by kind ("terms", "templates", "terminologies"):
    # carried, so not missing, though not loaded.
    carried: dict[str, frozenset[str]] = field(default_factory=dict)
    checked: int = 0
    # (template label, version, field, code) -> documents
    invalid: Counter = field(default_factory=Counter)
    invalid_samples: list[str] = field(default_factory=list)
    invalid_documents: int = 0
    # (template label, version) -> documents
    missing_versions: Counter = field(default_factory=Counter)
    missing_samples: list[str] = field(default_factory=list)
    identity_checked: int = 0
    identity_mismatches: Counter = field(default_factory=Counter)
    identity_samples: list[str] = field(default_factory=list)
    missing_terms: Counter = field(default_factory=Counter)
    missing_term_samples: list[str] = field(default_factory=list)
    # Template / terminology ids named by `references[].resolved` that are
    # neither carried nor referents, matched by id (a version is not judged).
    missing_templates: Counter = field(default_factory=Counter)
    missing_template_samples: list[str] = field(default_factory=list)
    missing_terminologies: Counter = field(default_factory=Counter)
    missing_terminology_samples: list[str] = field(default_factory=list)
    # (kind, ref) -> documents
    unverifiable: Counter = field(default_factory=Counter)
    unverifiable_samples: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._validator = OfflineValidator(self.definitions)
        self._resolved: dict[tuple[str, int], dict[str, Any] | UnresolvableTemplate] = {}
        self._template_ids = {tid for (tid, _v) in self.definitions.templates}
        # A template's label is its newest version's value.
        self._labels: dict[str, str] = {}
        for (tid, _v), row in sorted(self.definitions.templates.items(), key=lambda kv: kv[0][1]):
            if row.get("value"):
                self._labels[tid] = str(row["value"])

    def _label(self, template_id: str) -> str:
        return self._labels.get(template_id, template_id)

    def _template(self, template_id: str, version: int) -> dict[str, Any] | UnresolvableTemplate:
        key = (template_id, version)
        if key not in self._resolved:
            try:
                self._resolved[key] = self.definitions.resolve_template(
                    template_id, version
                ).as_template_dict()
            except UnresolvableTemplate as e:
                self._resolved[key] = e
        return self._resolved[key]

    def observe(self, row: dict[str, Any]) -> None:
        document_id = str(row.get("document_id") or "")
        template_id = str(row.get("template_id") or "")
        version = int(row.get("template_version") or 1)
        tag = f"{document_id} v{row.get('version', 1)}"
        self.checked += 1
        # A reference's target is judged whether or not the document's own
        # template can be: it needs nothing from that template.
        self._check_term_references(row, tag)
        self._check_definition_references(row, tag)

        if template_id not in self._template_ids:
            # The document is based on a template the archive does not carry
            # (e.g. a shared one in `wip`): its schema cannot be judged here.
            self.unverifiable[("template", template_id)] += 1
            _sample(self.unverifiable_samples, f"{tag}: template {template_id}")
            return

        template = self._template(template_id, version)
        label = self._label(template_id)
        if isinstance(template, UnresolvableTemplate):
            if template.missing == (template_id, version):
                self.missing_versions[(label, version)] += 1
                _sample(self.missing_samples, f"{tag}: {label} v{version}")
            else:
                ref = f"{template.missing[0]} v{template.missing[1]}" if template.missing else template_id
                self.unverifiable[("template", ref)] += 1
                _sample(self.unverifiable_samples, f"{tag}: {template.reason}")
            return

        namespace = str(row.get("namespace") or template.get("namespace") or "")
        outcome = self._validator.validate(
            template, row.get("data"), namespace, resolve_terms=False
        )
        if outcome.errors:
            self.invalid_documents += 1
            for error in outcome.errors:
                self.invalid[(label, version, error.get("field"), error.get("code"))] += 1
            first = outcome.errors[0]
            _sample(self.invalid_samples, f"{tag}: {first.get('field')} {first.get('code')}")
        for entry in outcome.unverifiable:
            self.unverifiable[(entry["kind"], entry["ref"])] += 1
            _sample(self.unverifiable_samples, f"{tag}: {entry['kind']} {entry['ref']}")

        self._check_identity(row, template, label, version, tag)

    def _check_term_references(self, row: dict[str, Any], tag: str) -> None:
        if not self.has_referents_table:
            return
        for ref in row.get("term_references") or ():
            term_id = ref.get("term_id")
            if not term_id or term_id in self.definitions.terms:
                continue
            if term_id in self.definitions.referents or term_id in self.carried.get("terms", ()):
                continue
            self.missing_terms[term_id] += 1
            _sample(self.missing_term_samples, f"{tag}: {ref.get('field_path')} → {term_id}")

    def _check_definition_references(self, row: dict[str, Any], tag: str) -> None:
        """Template- and terminology-typed references: their resolved target
        must be carried or a referent. Term-typed ones are not read here —
        a term field writes `term_references` too, checked above."""
        if not self.has_referents_table:
            return
        for ref in row.get("references") or ():
            resolved = ref.get("resolved") or {}
            template_id = resolved.get("template_id")
            if template_id and self._missing("templates", template_id, self._template_ids):
                self.missing_templates[template_id] += 1
                _sample(self.missing_template_samples, f"{tag}: {ref.get('field_path')} → {template_id}")
            terminology_id = resolved.get("terminology_id")
            if terminology_id and self._missing("terminologies", terminology_id, self.definitions.terminologies):
                self.missing_terminologies[terminology_id] += 1
                _sample(
                    self.missing_terminology_samples, f"{tag}: {ref.get('field_path')} → {terminology_id}"
                )

    def _missing(self, kind: str, entity_id: str, loaded: Any) -> bool:
        """An id neither loaded, carried elsewhere in the archive, nor a referent."""
        return (
            entity_id not in loaded
            and entity_id not in self.carried.get(kind, ())
            and entity_id not in self.definitions.referents
        )

    def _check_identity(
        self, row: dict[str, Any], template: dict[str, Any], label: str, version: int, tag: str,
    ) -> None:
        identity_fields = template.get("identity_fields") or []
        stored = row.get("identity_hash")
        if not identity_fields or not stored:
            return
        self.identity_checked += 1
        try:
            values = resolved_identity_values(
                row.get("data") or {}, list(identity_fields), list(template.get("fields") or []),
                row.get("term_references"), row.get("references"),
                template.get("referent_identity_paths"),
            )
            recomputed: str | None = compute_composite_key_hash(values)
        except (ValueError, KeyError, TypeError):
            recomputed = None
        if recomputed != stored:
            self.identity_mismatches[(label, version)] += 1
            reason = "not recomputable" if recomputed is None else "differs"
            _sample(self.identity_samples, f"{tag}: {reason}")

    # ------------------------------------------------------------------

    def findings_data(self) -> list[dict[str, Any]]:
        """Finding fields, for the model to wrap; most severe classes first."""
        out: list[dict[str, Any]] = []
        if self.missing_versions:
            out.append({
                "severity": "error",
                "finding_class": "template_version_missing",
                "subject": "documents",
                "detail": (
                    "documents are pinned to template versions the archive does not "
                    "carry, although it carries the template — they restore with no "
                    "schema behind them"
                ),
                "count": sum(self.missing_versions.values()),
                "samples": tuple(self.missing_samples),
            })
        if self.missing_terms:
            out.append({
                "severity": "error",
                "finding_class": "missing_term_reference",
                "subject": "documents",
                "detail": (
                    "stored term references point at terms neither in the archive nor "
                    "recorded as referenced outside it — they will not resolve after restore"
                ),
                "count": sum(self.missing_terms.values()),
                "samples": tuple(self.missing_term_samples),
            })
        for finding_class, kind, counter, samples in (
            ("missing_template_reference", "templates", self.missing_templates,
             self.missing_template_samples),
            ("missing_terminology_reference", "terminologies", self.missing_terminologies,
             self.missing_terminology_samples),
        ):
            if counter:
                out.append({
                    "severity": "error",
                    "finding_class": finding_class,
                    "subject": "documents",
                    "detail": (
                        f"stored references point at {kind} neither in the archive nor "
                        "recorded as referenced outside it — they will not resolve after restore"
                    ),
                    "count": sum(counter.values()),
                    "samples": tuple(samples),
                })
        if self.invalid_documents:
            out.append({
                "severity": "warning",
                "finding_class": "document_invalid",
                "subject": "documents",
                "detail": (
                    "documents fail their own pinned template under today's validation "
                    "rules (fields and cross-field rules); they restore, but a PATCH or "
                    "migration of them will be refused until they are corrected"
                ),
                "count": self.invalid_documents,
                "samples": tuple(self.invalid_samples),
            })
        if self.identity_mismatches:
            out.append({
                "severity": "warning",
                "finding_class": "identity_hash_mismatch",
                "subject": "documents",
                "detail": (
                    "stored identity_hash differs from the hash recomputed from the "
                    "document's data — typically an archive written before identity "
                    "fields hashed their resolved referents; after restoring, run "
                    "POST /api/document-store/maintenance/rehash-identities"
                ),
                "count": sum(self.identity_mismatches.values()),
                "samples": tuple(self.identity_samples),
            })
        if self.unverifiable:
            out.append({
                "severity": "warning",
                "finding_class": "unverifiable_dependency",
                "subject": "documents",
                "detail": (
                    "documents depend on templates or vocabularies the archive does not "
                    "carry, so those parts were not checked: "
                    + ", ".join(f"{kind} {ref}" for (kind, ref) in sorted(self.unverifiable)[:5])
                ),
                "count": sum(self.unverifiable.values()),
                "samples": tuple(self.unverifiable_samples),
            })
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "documents_checked": self.checked,
            "documents_invalid": self.invalid_documents,
            "invalid_by_template": [
                {"template": t, "version": v, "field": f, "code": c, "documents": n}
                for (t, v, f, c), n in sorted(
                    self.invalid.items(), key=lambda kv: (-kv[1], str(kv[0]))
                )
            ],
            "missing_template_versions": [
                {"template": t, "version": v, "documents": n}
                for (t, v), n in sorted(self.missing_versions.items(), key=str)
            ],
            "identity": {
                "checked": self.identity_checked,
                "mismatches": [
                    {"template": t, "version": v, "documents": n}
                    for (t, v), n in sorted(self.identity_mismatches.items(), key=str)
                ],
            },
            "missing_term_references": sum(self.missing_terms.values()),
            "missing_template_references": sum(self.missing_templates.values()),
            "missing_terminology_references": sum(self.missing_terminologies.values()),
            "unverifiable": [
                {"kind": k, "ref": r, "documents": n}
                for (k, r), n in sorted(self.unverifiable.items(), key=str)
            ],
        }
