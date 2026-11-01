"""The `split` plan: one archive per namespace, or one per island.

Each part is an ordinary transform output (``wip_archive.transform``): its
rows travel verbatim, a reference into another part becomes a referent, and
it is verified with its own ``derived_from`` hop.

- ``--by namespace``: one part per namespace the archive carries, holding
  that namespace's rows. A reference into another namespace of the archive
  becomes a referent, which a restore resolves where that namespace exists.
- ``--by island``: one part per island (``ArchiveModel.islands``): the
  templates a connected component joins, through declared and actual
  references alike, with every version of their documents, the files those
  documents reference, and the terminologies the island uses. Edge types
  sit in their endpoints' island, so relationships never cross a part. A
  terminology several islands use is copied into each (the model reports
  these as shared). A terminology no island uses, and a document pinned to
  a template the archive does not carry, go into a last ``unattached``
  part, so nothing is lost.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .archive import ArchiveReader
from .model import ArchiveModel

KeepRow = Callable[[str, str, dict[str, Any]], bool]

SPLIT_BY = ("namespace", "island")


class SplitError(ValueError):
    """The archive cannot be split as asked."""


@dataclass
class SplitPart:
    """One output archive: its name and the rows it keeps."""

    name: str
    description: str
    keep_row: KeepRow


@dataclass
class _IslandRows:
    templates: set[str] = field(default_factory=set)
    terminologies: set[str] = field(default_factory=set)
    terms: set[str] = field(default_factory=set)
    documents: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)

    def keep_row(self, _ns: str, entity_type: str, row: dict[str, Any]) -> bool:
        if entity_type == "templates":
            return row.get("template_id") in self.templates
        if entity_type == "documents":
            return row.get("document_id") in self.documents
        if entity_type == "terminologies":
            return row.get("terminology_id") in self.terminologies
        if entity_type == "terms":
            return row.get("term_id") in self.terms
        if entity_type == "term_relations":
            return row.get("source_term_id") in self.terms
        if entity_type == "files":
            return row.get("file_id") in self.files
        return False


def _by_namespace(model: ArchiveModel) -> list[SplitPart]:
    def keep_for(part_ns: str) -> KeepRow:
        return lambda ns, _entity_type, _row: ns == part_ns

    return [SplitPart(ns, f"namespace {ns}", keep_for(ns)) for ns in model.namespaces]


def _by_island(reader: ArchiveReader, model: ArchiveModel) -> list[SplitPart]:
    islands = model.islands()
    parts = [_IslandRows(templates=set(i.template_ids), terminologies=set(i.terminology_ids))
             for i in islands]
    island_of_template = {t: n for n, i in enumerate(islands) for t in i.template_ids}

    # Documents follow their template's island; files follow their documents.
    # A document pinned to a template the archive does not carry has no
    # island: it goes to the unattached part rather than nowhere.
    unattached = _IslandRows()
    for ns in model.namespaces:
        for row in reader.read_entities("documents", namespace=ns):
            n = island_of_template.get(row.get("template_id") or "")
            target = parts[n] if n is not None else unattached
            target.documents.add(row["document_id"])
            for fref in row.get("file_references") or ():
                if isinstance(fref.get("file_id"), str):
                    target.files.add(fref["file_id"])

    used = set().union(*(p.terminologies for p in parts)) if parts else set()
    unattached.terminologies = set(model.terminologies) - used
    has_unattached = bool(unattached.terminologies or unattached.documents)
    if has_unattached:
        parts.append(unattached)
    for part in parts:
        part.terms = {t for t, node in model.terms.items()
                      if node.terminology_id in part.terminologies}

    out: list[SplitPart] = []
    width = len(str(len(islands)))
    for n, island in enumerate(islands):
        labels = sorted(model.template_label(t) for t in island.template_ids)
        shown = ", ".join(labels[:3]) + (f" and {len(labels) - 3} more" if len(labels) > 3 else "")
        out.append(SplitPart(f"island-{n + 1:0{width}d}", f"island {n + 1}: {shown}",
                             parts[n].keep_row))
    if has_unattached:
        out.append(SplitPart(
            "unattached",
            f"{len(unattached.terminologies)} terminologies no island uses, "
            f"{len(unattached.documents)} documents whose template the archive does not carry",
            unattached.keep_row,
        ))
    return out


def plan_split(reader: ArchiveReader, model: ArchiveModel, by: str = "namespace") -> list[SplitPart]:
    if by not in SPLIT_BY:
        raise SplitError(f"unknown --by {by!r}; one of {', '.join(SPLIT_BY)}")
    parts = _by_namespace(model) if by == "namespace" else _by_island(reader, model)
    if not parts:
        raise SplitError("the archive carries nothing to split")
    return parts
