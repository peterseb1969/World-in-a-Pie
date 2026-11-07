"""Every id a restore re-mints is followed wherever an archived row names it
(CASE-1108).

- A term- or terminology-reference keeps its referent's id in
  ``references[].resolved``, and the identity hash reads that copy
  (references are applied after term_references): left on the source's id,
  the next write of the same data is a second document.
- A term's successor (``replaced_by_term_id``) names a term like its parent.
- A terminology id written as a reference string (a lookup value, a data
  value) follows the terminology map like the other id kinds.

The drift guard below takes the slots from ``referents.referenced_ids``,
the one list of where archived rows name other entities: a slot added there
without a remap rule fails here, not in a restore.
"""

from __future__ import annotations

import itertools

import pytest
from wip_archive.identity import build_referent_resolutions
from wip_archive.referents import referenced_ids
from wip_archive.remap import IDRemapper


def _remapper() -> IDRemapper:
    r = IDRemapper({"src": "tgt"})
    r.add_term_mapping("T-OLD", "T-NEW")
    r.add_term_mapping("P-OLD", "P-NEW")
    r.add_terminology_mapping("V-OLD", "V-NEW")
    return r


def test_a_term_reference_identity_reads_the_mapped_term():
    doc = {
        "template_id": "X", "data": {"kind": "foo"},
        "term_references": [{"field_path": "kind", "term_id": "T-OLD", "terminology_ref": "KIND"}],
        "references": [{
            "field_path": "kind", "reference_type": "term", "lookup_value": "foo",
            "resolved": {"term_id": "T-OLD", "terminology_id": "V-OLD", "terminology_value": "KIND"},
        }],
    }
    out = _remapper().remap_document(doc)
    assert out["references"][0]["resolved"] == {
        "term_id": "T-NEW", "terminology_id": "V-NEW", "terminology_value": "KIND",
    }
    assert build_referent_resolutions(out["term_references"], out["references"]) == {"kind": "T-NEW"}
    assert doc["references"][0]["resolved"]["term_id"] == "T-OLD"  # input untouched


def test_a_terminology_reference_reads_the_mapped_terminology():
    doc = {
        "template_id": "X", "data": {"voc": "V-OLD"},
        "references": [{
            "field_path": "voc", "reference_type": "terminology", "lookup_value": "V-OLD",
            "resolved": {"terminology_id": "V-OLD"},
        }],
    }
    out = _remapper().remap_document(doc)
    ref = out["references"][0]
    assert ref["resolved"] == {"terminology_id": "V-NEW"} and ref["lookup_value"] == "V-NEW"
    assert out["data"]["voc"] == "V-NEW"
    assert build_referent_resolutions([], out["references"]) == {"voc": "V-NEW"}


def test_a_terms_successor_and_parent_follow_the_term_map():
    out = _remapper().remap_term({
        "term_id": "T1", "terminology_id": "V-OLD", "parent_term_id": "P-OLD",
        "replaced_by_term_id": "T-OLD",
    })
    assert (out["terminology_id"], out["parent_term_id"], out["replaced_by_term_id"]) == (
        "V-NEW", "P-NEW", "T-NEW",
    )


def test_ids_outside_the_maps_pass_through():
    out = _remapper().remap_document({
        "template_id": "X", "data": {},
        "references": [{"field_path": "k", "reference_type": "term",
                        "resolved": {"term_id": "elsewhere", "terminology_id": "wip-voc"}}],
    })
    assert out["references"][0]["resolved"] == {"term_id": "elsewhere", "terminology_id": "wip-voc"}


# ── the drift guard ────────────────────────────────────────────────────────

_ids = (f"OLD-{n}" for n in itertools.count())


def _rows() -> dict[str, dict]:
    """One row per entity type with every slot referenced_ids reads set to a
    fresh old id."""
    field = {
        "name": "f", "terminology_ref": next(_ids), "array_terminology_ref": next(_ids),
        "template_ref": next(_ids), "array_template_ref": next(_ids),
        "target_templates": [next(_ids)], "target_terminologies": [next(_ids)],
    }
    return {
        "terms": {"term_id": "self", "terminology_id": next(_ids), "parent_term_id": next(_ids),
                  "replaced_by_term_id": next(_ids)},
        "term_relations": {"source_term_id": next(_ids), "target_term_id": next(_ids)},
        "templates": {
            "template_id": "self", "extends": next(_ids), "fields": [field],
            "source_templates": [{"lookup_value": "S", "resolved": next(_ids)}],
            "target_templates": [{"lookup_value": "T", "resolved": next(_ids)}],
        },
        "documents": {
            "template_id": next(_ids), "data": {},
            "term_references": [{"field_path": "a", "term_id": next(_ids), "terminology_ref": next(_ids)}],
            "references": [{"field_path": "b", "reference_type": "document", "resolved": {
                "document_id": next(_ids), "template_id": next(_ids), "term_id": next(_ids),
                "terminology_id": next(_ids),
            }}],
            "file_references": [{"field_path": "c", "file_id": next(_ids)}],
        },
    }


_REMAP = {
    "terms": IDRemapper.remap_term,
    "term_relations": IDRemapper.remap_term_relation,
    "templates": IDRemapper.remap_template,
    "documents": IDRemapper.remap_document,
}


@pytest.mark.parametrize("entity_type", sorted(_REMAP))
def test_every_slot_referents_names_is_remapped(entity_type):
    row = _rows()[entity_type]
    slots = [i for i in referenced_ids(entity_type, row) if i.startswith("OLD-")]
    assert slots, "the row sets no slot referenced_ids reads"
    remapper = IDRemapper()
    for mapping in (remapper.term_map, remapper.terminology_map, remapper.template_map,
                    remapper.document_map, remapper.file_map):
        mapping.update({i: f"NEW-{i}" for i in slots})
    out = _REMAP[entity_type](remapper, row)
    left = [i for i in referenced_ids(entity_type, out) if i.startswith("OLD-")]
    assert left == [], f"{entity_type}: still the source's ids after remap: {left}"
