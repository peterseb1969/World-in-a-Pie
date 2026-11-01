"""The `prune` plan: drop the templates nothing uses, and the terminologies
only they used.

``prune`` answers the deletion question, where ``filter`` answers the
extraction one. Nothing that has data is touched: every document and file
travels.

**A template is used** when documents are pinned to it, or when the schema
of a template with documents needs it: as a parent, a reference target or an
edge-type endpoint, transitively (``ArchiveModel.schema_closure``). Every
other template is pruned, all its versions. Computing usage from the
templates that hold data, rather than counting each template's declared
in-degree, also removes unused templates that only declare each other.

**A terminology is pruned** only when a pruned template declared it and
nothing kept uses it: no kept template declares it, no kept document holds
one of its terms, and no term relation from a kept terminology points into
it. A terminology no template ever declared is kept: an app may use it
through the validation API alone, which an archive cannot see.

A pruned entity is referenced by nothing that travels, so it leaves no
referent behind, and the output verifies as the input did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .archive import ArchiveReader
from .model import ArchiveModel


@dataclass
class PrunePlan:
    """What prune removes; everything else travels."""

    templates: set[str] = field(default_factory=set)
    terminologies: set[str] = field(default_factory=set)
    terms: set[str] = field(default_factory=set)  # the pruned terminologies' terms
    template_names: list[str] = field(default_factory=list)
    terminology_names: list[str] = field(default_factory=list)

    def keep_row(self, _namespace: str, entity_type: str, row: dict[str, Any]) -> bool:
        if entity_type == "templates":
            return row.get("template_id") not in self.templates
        if entity_type == "terminologies":
            return row.get("terminology_id") not in self.terminologies
        if entity_type == "terms":
            return row.get("terminology_id") not in self.terminologies
        if entity_type == "term_relations":
            # A relation belongs to its source term.
            return row.get("source_term_id") not in self.terms
        return True

    def summary(self) -> dict[str, Any]:
        return {
            "templates_pruned": self.template_names,
            "terminologies_pruned": self.terminology_names,
        }


def plan_prune(reader: ArchiveReader, model: ArchiveModel) -> PrunePlan:
    with_data = {tid for tid, tpl in model.templates.items() if tpl.document_ids}
    closure = model.schema_closure(with_data)
    used_templates = with_data | {t for t in closure.template_ids if t in model.templates}
    pruned_templates = set(model.templates) - used_templates

    used_terminologies = set(closure.terminology_ids)
    for term_id, count in model.history.term_use.items():
        term = model.terms.get(term_id)
        if count and term is not None:
            used_terminologies.add(term.terminology_id)

    declared_by_pruned: set[str] = set()
    for template_id in pruned_templates:
        declared_by_pruned |= model.declared_terminologies(template_id)
    candidates = {t for t in declared_by_pruned - used_terminologies if t in model.terminologies}

    # A term relation from a kept terminology into a candidate keeps the
    # candidate, and a candidate kept that way keeps what it relates to.
    if candidates:
        links: set[tuple[str, str]] = set()
        for ns in model.namespaces:
            for row in reader.read_entities("term_relations", namespace=ns):
                source = model.terms.get(row.get("source_term_id") or "")
                target = model.terms.get(row.get("target_term_id") or "")
                if source is not None and target is not None:
                    links.add((source.terminology_id, target.terminology_id))
        changed = True
        while changed:
            changed = False
            for source_tl, target_tl in links:
                if source_tl not in candidates and target_tl in candidates:
                    candidates.discard(target_tl)
                    changed = True

    plan = PrunePlan(templates=pruned_templates, terminologies=candidates)
    plan.terms = {t for t, node in model.terms.items() if node.terminology_id in candidates}
    plan.template_names = sorted(
        f"{model.templates[t].namespace}:{model.templates[t].label}" for t in pruned_templates
    )
    plan.terminology_names = sorted(
        f"{model.terminologies[t].namespace}:{model.terminologies[t].label}" for t in candidates
    )
    return plan
