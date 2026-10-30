"""Out-of-archive referents: what an archive points at but does not carry.

An archived entity can reference one the archive does not hold: a template
whose term field draws from the shared ``wip`` vocabularies, a document
pointing into a sibling namespace, a term relation into another ontology.
The reference stores only the target's canonical id, which names an entity
on the source install. On another install that id means nothing, or the
wrong thing, and nothing else in the archive says which entity was meant.

The referent table closes that gap. For every id an archived entity
references and the archive does not carry, it holds the target's Registry
entry row, in the same shape as ``registry_entries.jsonl``. The row carries
the entity's primary composite key and synonyms, which are its identity in a
form another install can resolve: ``{ns, type, value}`` for templates and
terminologies, the terminology-scoped key for terms, the template-qualified
identity synonym for documents. Choosing a key is left to the restore; the
table records everything the Registry knew.

Collection is deliberately mechanical. Every string in a known reference
slot is a candidate, the archive's own Registry entries are subtracted, and
the rest is looked up by ``entry_id``. A slot holding a value rather than an
id (a template's legacy value-form reference) matches no entry and drops
out, so nothing is inferred from a spelling.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

# Keys of a resolved document reference that hold canonical ids.
_RESOLVED_ID_KEYS = ("document_id", "template_id", "term_id", "terminology_id")

# Field-level template slots holding one id, and those holding a list of ids.
_FIELD_SCALAR_SLOTS = (
    "terminology_ref",
    "array_terminology_ref",
    "template_ref",
    "array_template_ref",
)
_FIELD_LIST_SLOTS = ("target_templates", "target_terminologies")


def _strings(values: Iterable[Any]) -> Iterator[str]:
    for value in values:
        if isinstance(value, str) and value:
            yield value


def _endpoint_ids(entries: Iterable[Any]) -> Iterator[str]:
    """Ids of edge-type endpoint entries: the resolved half of a two-half
    entry, or a legacy bare string."""
    for entry in entries:
        if isinstance(entry, dict):
            entry = entry.get("resolved")
        if isinstance(entry, str) and entry:
            yield entry


def referenced_ids(entity_type: str, row: dict[str, Any]) -> Iterator[str]:
    """Every id the archived row references, by the slot it sits in.

    Covers the slots a restore remaps: a term's terminology and parent, a
    relation's two terms, a template's field references, parent and edge
    endpoints, and a document's template, term, reference and file
    references. Yields candidates only; a candidate may be carried by the
    archive or be no id at all.
    """
    if entity_type == "terms":
        yield from _strings((row.get("terminology_id"), row.get("parent_term_id")))
    elif entity_type == "term_relations":
        yield from _strings((row.get("source_term_id"), row.get("target_term_id")))
    elif entity_type == "templates":
        yield from _strings((row.get("extends"),))
        for field in row.get("fields") or []:
            yield from _strings(field.get(slot) for slot in _FIELD_SCALAR_SLOTS)
            for slot in _FIELD_LIST_SLOTS:
                yield from _strings(field.get(slot) or [])
        yield from _endpoint_ids(row.get("source_templates") or [])
        yield from _endpoint_ids(row.get("target_templates") or [])
    elif entity_type == "documents":
        yield from _strings((row.get("template_id"),))
        for tref in row.get("term_references") or []:
            yield from _strings((tref.get("term_id"), tref.get("terminology_ref")))
        for ref in row.get("references") or []:
            resolved = ref.get("resolved") or {}
            yield from _strings(resolved.get(key) for key in _RESOLVED_ID_KEYS)
        for fref in row.get("file_references") or []:
            yield from _strings((fref.get("file_id"),))


class ReferentCollector:
    """Accumulates, over an archive's rows, the ids it references but does
    not carry.

    Feed it every row written, in any order; ``registry_entries`` rows
    define what the archive carries. Memory is one set of referenced ids and
    one of carried ids, the same order as the document index the offline
    model already holds.
    """

    def __init__(self) -> None:
        self._referenced: set[str] = set()
        self._carried: set[str] = set()

    def add(self, entity_type: str, row: dict[str, Any]) -> None:
        if entity_type == "registry_entries":
            entry_id = row.get("entry_id")
            if isinstance(entry_id, str):
                self._carried.add(entry_id)
            return
        self._referenced.update(referenced_ids(entity_type, row))

    def external_ids(self) -> list[str]:
        """Referenced ids the archive does not carry, sorted, so the table
        is a pure function of the archive's content."""
        return sorted(self._referenced - self._carried)
