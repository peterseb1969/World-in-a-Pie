"""The `filter` plan: which rows travel when an archive is cut down to some
templates.

A plan is computed before anything is written, from the archive model plus
one streaming pass over the documents, and is then applied by the shared
transform core (``wip_archive.transform``), which copies the kept rows
verbatim and turns cut entities into referents.

**What is selected.** Every version of every document of the named
templates. With ``relationships`` (the default), every relationship
document whose ``source_ref`` is one of those documents comes along too:
``/relationships`` and ``/traverse`` are visible to apps, so leaving a
selected document's relationships behind would change what an app sees.
Relationships follow the template selection only, not documents a boundary
policy adds.

**The boundary policy** decides what happens to a document reference from
a kept document to a document the archive carries but the selection does
not keep:

- ``strict`` refuses the plan when any reference crosses;
- ``drop`` keeps the reference and cuts the target (it becomes a referent);
- ``stubs`` keeps each such target too, one hop out; the stubs' own
  references cross as under ``drop``;
- ``closure`` keeps following until no reference crosses. It can reach the
  whole archive, so the plan reports how far it went before anything is
  written.

**Definitions always close.** The policy covers documents only. Every
template a kept document pins travels, with all its versions, everything it
declares (parents, reference targets, edge-type endpoints) transitively, and
every terminology those templates declare or the kept documents' terms
belong to, each whole. Templates and terminologies a kept document names
through template- or terminology-typed reference fields travel as
definitions too: the boundary policy covers documents only. Terms travel with their terminology, term relations
with their source term, files with the kept documents that reference them.

With ``definitions_only`` no document or file travels: the definitions of
the named templates (or, with no names, every definition) and nothing else.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .archive import ArchiveReader
from .model import AmbiguousNameError, ArchiveModel

BOUNDARIES = ("strict", "drop", "stubs", "closure")

_SAMPLE_CAP = 5


class FilterError(ValueError):
    """The selection cannot be planned (an unknown or ambiguous name)."""


class BoundaryViolation(FilterError):
    """``strict``: references cross the selection's boundary."""

    def __init__(self, plan: FilterPlan) -> None:
        self.plan = plan
        super().__init__(
            f"{plan.cut_references} reference(s) from kept documents point at "
            f"{len(plan.cut_targets)} document(s) the selection does not keep; "
            f"e.g. {', '.join(plan.cut_samples)}"
        )


@dataclass
class FilterPlan:
    """The rows a filter keeps, and how the selection came about."""

    boundary: str
    definitions_only: bool
    templates: set[str] = field(default_factory=set)
    terminologies: set[str] = field(default_factory=set)
    terms: set[str] = field(default_factory=set)
    documents: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)
    selected_documents: int = 0
    relationship_documents: int = 0
    added_documents: int = 0  # by stubs or closure
    cut_references: int = 0
    cut_targets: set[str] = field(default_factory=set)
    cut_samples: list[str] = field(default_factory=list)
    archive_documents: int = 0

    def keep_row(self, _namespace: str, entity_type: str, row: dict[str, Any]) -> bool:
        if entity_type == "documents":
            return row.get("document_id") in self.documents
        if entity_type == "templates":
            return row.get("template_id") in self.templates
        if entity_type == "terminologies":
            return row.get("terminology_id") in self.terminologies
        if entity_type == "terms":
            return row.get("term_id") in self.terms
        if entity_type == "term_relations":
            return row.get("source_term_id") in self.terms
        if entity_type == "files":
            return row.get("file_id") in self.files
        return False

    def summary(self) -> dict[str, Any]:
        """The filter-specific counts recorded in the hop's summary."""
        return {
            "selected_documents": self.selected_documents,
            "relationship_documents": self.relationship_documents,
            "added_documents": self.added_documents,
            "cut_references": self.cut_references,
            "cut_documents": len(self.cut_targets),
        }


def resolve_templates(model: ArchiveModel, names: list[str]) -> set[str]:
    """Template ids for the names a caller gave: an id, ``ns:VALUE``, or a
    bare value. A bare value carried by several namespaces is refused, never
    picked (``ArchiveModel.resolve_template``)."""
    out: set[str] = set()
    for name in names:
        try:
            template = model.resolve_template(name)
        except AmbiguousNameError as e:
            raise FilterError(str(e)) from e
        if template is None:
            raise FilterError(f"no template {name!r} in this archive")
        out.add(template.template_id)
    return out


def plan_filter(
    reader: ArchiveReader,
    model: ArchiveModel,
    template_names: list[str],
    *,
    boundary: str = "drop",
    relationships: bool = True,
    definitions_only: bool = False,
) -> FilterPlan:
    """Plan a filter of the archive ``reader`` reads, whose model is
    ``model``. Raises FilterError on an unknown or ambiguous name and
    BoundaryViolation under ``strict`` when references cross."""
    if boundary not in BOUNDARIES:
        raise FilterError(f"unknown boundary {boundary!r}; one of {', '.join(BOUNDARIES)}")
    named = resolve_templates(model, template_names)
    if not named and not definitions_only:
        raise FilterError("name at least one template, or pass definitions_only")
    plan = FilterPlan(boundary=boundary, definitions_only=definitions_only,
                      archive_documents=len(model.documents))

    if definitions_only:
        roots = named or set(model.templates)
        _close_definitions(model, plan, roots, set())
        if not named:
            plan.terminologies = set(model.terminologies)
            plan.terms = set(model.terms)
        return plan

    references, edges_by_source = _document_graph(reader, model)

    selected = {doc_id for doc_id, entry in model.documents.items()
                if entry.template_id in named}
    plan.selected_documents = len(selected)
    kept = set(selected)
    if relationships:
        for doc_id in selected:
            kept |= edges_by_source.get(doc_id, set())
        plan.relationship_documents = len(kept) - len(selected)

    before = len(kept)
    if boundary == "stubs":
        kept |= {t for d in list(kept) for t in references.get(d, ())
                 if t in model.documents}
    elif boundary == "closure":
        queue = deque(kept)
        while queue:
            for target in references.get(queue.popleft(), ()):
                if target in model.documents and target not in kept:
                    kept.add(target)
                    queue.append(target)
    plan.added_documents = len(kept) - before
    plan.documents = kept

    for doc_id in sorted(kept):
        for target in sorted(references.get(doc_id, ())):
            if target in model.documents and target not in kept:
                plan.cut_references += 1
                plan.cut_targets.add(target)
                if len(plan.cut_samples) < _SAMPLE_CAP:
                    plan.cut_samples.append(f"{doc_id} → {target}")

    pinned, doc_terminologies = _kept_document_dependencies(reader, model, plan)
    _close_definitions(model, plan, named | pinned, doc_terminologies)

    if boundary == "strict" and plan.cut_references:
        raise BoundaryViolation(plan)
    return plan


def _document_graph(
    reader: ArchiveReader, model: ArchiveModel
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """One pass over every document row: each document's document
    references (all versions merged), and each source's relationship
    documents."""
    references: dict[str, set[str]] = {}
    edges_by_source: dict[str, set[str]] = {}
    for ns in model.namespaces:
        for row in reader.read_entities("documents", namespace=ns):
            doc_id = row.get("document_id")
            if not isinstance(doc_id, str):
                continue
            template = model.templates.get(row.get("template_id") or "")
            is_edge = template is not None and template.is_edge_type
            for ref in row.get("references") or ():
                if ref.get("reference_type") != "document":
                    continue
                target = (ref.get("resolved") or {}).get("document_id")
                if not isinstance(target, str) or not target:
                    continue
                references.setdefault(doc_id, set()).add(target)
                if is_edge and ref.get("field_path") == "source_ref":
                    edges_by_source.setdefault(target, set()).add(doc_id)
    return references, edges_by_source


def _kept_document_dependencies(
    reader: ArchiveReader, model: ArchiveModel, plan: FilterPlan
) -> tuple[set[str], set[str]]:
    """A pass over the kept documents: the templates they pin or name, the
    terminologies of the terms they hold or that they name, and (into the
    plan) their files."""
    pinned: set[str] = set()
    terminologies: set[str] = set()
    for ns in model.namespaces:
        for row in reader.read_entities("documents", namespace=ns):
            if row.get("document_id") not in plan.documents:
                continue
            if isinstance(row.get("template_id"), str):
                pinned.add(row["template_id"])
            for tref in row.get("term_references") or ():
                term = model.terms.get(tref.get("term_id") or "")
                if term is not None:
                    terminologies.add(term.terminology_id)
            for ref in row.get("references") or ():
                resolved = ref.get("resolved") or {}
                if ref.get("reference_type") == "template":
                    target = resolved.get("template_id")
                    if isinstance(target, str) and target in model.templates:
                        pinned.add(target)
                elif ref.get("reference_type") == "terminology":
                    target = resolved.get("terminology_id")
                    if isinstance(target, str) and target in model.terminologies:
                        terminologies.add(target)
            for fref in row.get("file_references") or ():
                file_id = fref.get("file_id")
                if isinstance(file_id, str) and file_id in model.files:
                    plan.files.add(file_id)
    return pinned, terminologies


def _close_definitions(
    model: ArchiveModel, plan: FilterPlan, roots: set[str], doc_terminologies: set[str]
) -> None:
    carried = {t for t in roots if t in model.templates}
    closure = model.schema_closure(carried)
    plan.templates = {t for t in carried | closure.template_ids if t in model.templates}
    plan.terminologies = {
        t for t in closure.terminology_ids | doc_terminologies if t in model.terminologies
    }
    plan.terms = {
        term_id for term_id, term in model.terms.items()
        if term.terminology_id in plan.terminologies
    }
