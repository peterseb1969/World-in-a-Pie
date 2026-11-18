"""align_templates decides versions only on rows the merge writes, so an edge
that drop_unresolved_edges leaves out decides nothing.

Provenance: CASE-1409 (review of 2a42ed0f, R1). The unresolved-edge drops
were decided after the alignment, so an edge the merge then left out still
counted as arriving: here the dropped edge E_bad fills `weight`, which the
target's LINK v1 lacks, so the archive's v2 was unplaced and the edge E_ok,
whose endpoints resolve and which leaves `weight` empty, was left out with
it. The drops are now decided first and excluded like the rows a clash
policy holds back.
"""

from __future__ import annotations

import pytest

from document_store.services.backup_engine import COLLECTION_MAP

from .test_restore_unresolved_references import (  # noqa: F401  (fixtures)
    MISSING,
    OTHER,
    SOURCE,
    _doc_ref,
    _document,
    _documents,
    _entities,
    _run,
    live_registry,
    merge_target,
)

_REF_FIELDS = [
    {"name": "source_ref", "type": "reference", "reference_type": "document",
     "target_templates": ["NODE"], "mandatory": True},
    {"name": "target_ref", "type": "reference", "reference_type": "document",
     "target_templates": ["NODE"], "mandatory": True},
]
_WEIGHT = {"name": "weight", "type": "number"}


def _link(template_id: str, version: int, fields: list[dict]) -> dict:
    return {"template_id": template_id, "namespace": SOURCE, "value": "LINK",
            "version": version, "status": "active", "usage": "relationship",
            "identity_fields": ["source_ref", "target_ref"],
            "source_templates": ["NODE"], "target_templates": ["NODE"],
            "fields": fields}


def _edge(doc_id: str, target: str, target_ns: str, data: dict) -> dict:
    row = _document(doc_id, "TPL-LINK", 1, {"source_ref": "DOC-NODE", "target_ref": target, **data},
                    [_doc_ref("source_ref", "DOC-NODE", SOURCE), _doc_ref("target_ref", target, target_ns)],
                    f"h-{doc_id}")
    return {**row, "template_version": 2}


def _archive_entities(sorted_ids: bool) -> dict:
    """LINK v1 → v2 (v2 adds `weight`); E_bad points at a document no
    install holds and fills `weight`; E_ok links DOC-NODE to itself. Sorted by
    document_id the merge streams the chains; unsorted it holds them all."""
    entities = _entities()
    entities["templates"] = [
        t for t in entities["templates"] if t["template_id"] not in ("TPL-LINK", "TPL-HOLDER")
    ] + [_link("TPL-LINK", 1, _REF_FIELDS), _link("TPL-LINK", 2, [*_REF_FIELDS, _WEIGHT])]
    entities["documents"] = [
        d for d in entities["documents"] if d["document_id"] == "DOC-NODE"
    ] + [
        _edge("E-BAD", MISSING, OTHER, {"weight": 3}),
        _edge("E-OK", "DOC-NODE", SOURCE, {}),
    ]
    if not sorted_ids:
        entities["documents"].reverse()
    keep = {"TPL-NODE", "TPL-LINK", "DOC-NODE"}
    entities["registry_entries"] = [
        r for r in entities["registry_entries"] if r["entry_id"] in keep
    ] + [
        {**r, "entry_id": doc_id}
        for r in entities["registry_entries"] if r["entry_id"] == "DOC-NODE"
        for doc_id in ("E-BAD", "E-OK")
    ]
    return entities


@pytest.mark.asyncio
@pytest.mark.parametrize("chains", ["streamed", "held"])
async def test_a_dropped_edge_does_not_decide_the_version(merge_target, chains: str):  # noqa: F811
    db_name, coll_name = COLLECTION_MAP["templates"]
    await merge_target[db_name][coll_name].insert_one(_link("TPL-LINK-HERE", 1, _REF_FIELDS))

    engine, _ = await _run(
        merge_target, "run_merge", target_namespace=SOURCE, entities=_archive_entities(chains == "streamed"),
        add_missing=True, align_templates=True, drop_unresolved_edges=True,
    )

    result = engine.result["namespaces"][SOURCE]
    assert [e["document_id"] for e in result["referents"]["dropped_edges"]] == ["E-BAD"]
    report = result["template_alignment"]
    assert report["unplaceable_count"] == 0, report
    link = next(t for t in report["templates"] if t["value"] == "LINK")
    v2 = next(v for v in link["versions"] if v["archive_version"] == 2)
    assert (v2["outcome"], v2["target_version"], v2["rows"]) == ("mapped", 1, 1), v2
    rows = {r["document_id"]: r for r in await _documents(merge_target, SOURCE)}
    assert sorted(rows) == ["DOC-NODE", "E-OK"]
    assert (rows["E-OK"]["template_id"], rows["E-OK"]["template_version"]) == ("TPL-LINK-HERE", 1)
