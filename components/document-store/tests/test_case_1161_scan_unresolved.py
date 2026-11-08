"""The unresolved-reference scan of a restore, pinned before its refactor.

Provenance: CASE-1161. _scan_unresolved_references reads the archived
documents once and returns who references each unresolved referent and
which edges to drop. Pinned here on a fake archive: references are grouped
per (namespace, referent) with sorted, capped samples; an edge with an
unresolved document endpoint is dropped as a whole document (every
version), with its endpoints filled from its references; a referent whose
only referrers are dropped edges is not reported again (the branch the
suite did not reach); and with no edge templates every hit is a reference.
"""

from __future__ import annotations

from typing import Any

from document_store.services.backup_engine import (
    _REFERENCE_SAMPLE_CAP,
    _scan_unresolved_references,
    _UnresolvedReferent,
)

UNRESOLVED = {
    "doc-gone": _UnresolvedReferent("documents", "document doc-gone"),
    "term-gone": _UnresolvedReferent("terms", "term term-gone"),
}


class _Reader:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]):
        self.rows = rows

    def read_entities(self, kind: str, namespace: str):
        assert kind == "documents"
        return iter(self.rows.get(namespace, []))


def _doc_ref(field: str, doc_id: str) -> dict:
    return {"field_path": field, "resolved": {"document_id": doc_id}}


def _row(doc_id: str, template: str, *, refs=(), terms=()) -> dict:
    return {
        "document_id": doc_id, "template_id": template,
        "references": list(refs),
        "term_references": [{"field_path": f, "term_id": t} for f, t in terms],
    }


def test_references_are_grouped_and_edges_with_a_missing_endpoint_are_dropped():
    rows = {
        "ns-a": [
            _row("d1", "T", terms=[("colour", "term-gone"), ("shade", "term-gone")]),
            _row("d2", "T", refs=[_doc_ref("owner", "doc-gone")]),
            # An edge whose target is gone: dropped, both versions.
            _row("e1", "EDGE", refs=[_doc_ref("source_ref", "d1"), _doc_ref("target_ref", "doc-gone")]),
            _row("e1", "EDGE", refs=[_doc_ref("source_ref", "d1"), _doc_ref("target_ref", "doc-gone")]),
            # An edge whose v1 holds only an ordinary unresolved reference and
            # whose v2 loses its target: dropped, and its v1 reference is not
            # reported, so term-gone in ns-a is reported for d1 alone.
            _row("e2", "EDGE", refs=[_doc_ref("source_ref", "d2"), _doc_ref("target_ref", "d1")],
                 terms=[("kind", "term-gone")]),
            _row("e2", "EDGE", refs=[_doc_ref("source_ref", "d2"), _doc_ref("target_ref", "doc-gone")]),
        ],
        "ns-b": [
            # Only a dropped edge references term-gone here (its v1, before its
            # v2 loses its source): nothing reported.
            _row("e3", "EDGE", refs=[_doc_ref("source_ref", "d9"), _doc_ref("target_ref", "x")],
                 terms=[("kind", "term-gone")]),
            _row("e3", "EDGE", refs=[_doc_ref("source_ref", "doc-gone"), _doc_ref("target_ref", "x")]),
            _row("d9", "T"),
        ],
    }
    references, dropped = _scan_unresolved_references(
        _Reader(rows), ["ns-a", "ns-b"], UNRESOLVED, {"EDGE"},
    )
    assert references == [
        {"referent": "doc-gone", "entity_type": "documents", "description": "document doc-gone",
         "namespace": "ns-a", "documents": 1,
         "samples": [{"document_id": "d2", "field_path": "owner"}]},
        {"referent": "term-gone", "entity_type": "terms", "description": "term term-gone",
         "namespace": "ns-a", "documents": 1,
         "samples": [{"document_id": "d1", "field_path": "colour"},
                     {"document_id": "d1", "field_path": "shade"}]},
    ]
    assert dropped == {
        "e1": {"namespace": "ns-a", "document_id": "e1", "template_id": "EDGE",
               "source_ref": "d1", "target_ref": "doc-gone", "unresolved_endpoints": ["doc-gone"]},
        "e2": {"namespace": "ns-a", "document_id": "e2", "template_id": "EDGE",
               "source_ref": "d2", "target_ref": "doc-gone", "unresolved_endpoints": ["doc-gone"]},
        "e3": {"namespace": "ns-b", "document_id": "e3", "template_id": "EDGE",
               "source_ref": "doc-gone", "target_ref": "x", "unresolved_endpoints": ["doc-gone"]},
    }


def test_without_edge_templates_every_hit_is_a_reference_and_samples_are_capped():
    many = [_row(f"d{i:03d}", "T", refs=[_doc_ref("owner", "doc-gone")]) for i in range(_REFERENCE_SAMPLE_CAP + 5)]
    edge = _row("e1", "EDGE", refs=[_doc_ref("target_ref", "doc-gone")])
    references, dropped = _scan_unresolved_references(
        _Reader({"ns": [*many, edge]}), ["ns"], UNRESOLVED, set(),
    )
    assert dropped == {}
    [report] = references
    assert report["documents"] == _REFERENCE_SAMPLE_CAP + 6
    assert len(report["samples"]) == _REFERENCE_SAMPLE_CAP
    assert report["samples"][0] == {"document_id": "d000", "field_path": "owner"}


def test_an_edge_endpoint_to_a_missing_term_is_not_a_drop():
    # Only a document endpoint drops an edge.
    row = _row("e1", "EDGE", refs=[{"field_path": "target_ref", "resolved": {"term_id": "term-gone"}}])
    references, dropped = _scan_unresolved_references(_Reader({"ns": [row]}), ["ns"], UNRESOLVED, {"EDGE"})
    assert dropped == {}
    assert [(r["referent"], r["samples"]) for r in references] == [
        ("term-gone", [{"document_id": "e1", "field_path": "target_ref"}]),
    ]
