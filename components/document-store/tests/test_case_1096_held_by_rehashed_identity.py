"""Under align_templates and on_clash=skip, a document the target holds is
recognised by the identity hash the merge would write, not the archived one.

A term-typed identity field hashes the canonical id of the term it resolves
to. When the merge maps the archive's term onto the target's own copy of it,
the archived document's identity hash changes: the planner rehashes it before
matching it against the target. The held-document probe of the template
alignment matched on the archived hash instead, so it missed the target's
copy, counted the document as arriving, and let a field only that skipped
document fills mark its archive version unplaceable, which left out every
document pinned to that version.
"""

from __future__ import annotations

import copy

import pytest

from document_store.services.remap_restore import rehash_document_row
from tests.test_case_1043_align_templates import (  # noqa: F401  (mongo is a fixture)
    NOTE,
    _result,
    _target,
    _tpl,
    mongo,
)
from tests.test_merge_restore import NAMESPACE, _archive, _rows, _run_merge, _seed

UNIT_A = {"name": "unit", "type": "term", "terminology_ref": "A-lov"}
UNIT_B = {"name": "unit", "type": "term", "terminology_ref": "B-lov"}


def _hashed(row: dict, fields: list[dict]) -> dict:
    """``row`` with the identity hash a write computes for it."""
    row = copy.deepcopy(row)
    row["identity_hash"] = ""
    rehash_document_row(row, ["unit"], fields)
    return row


def _row(doc_id: str, template_id: str, version: int, term_id: str, lov: str, data: dict) -> dict:
    return {
        "document_id": doc_id, "namespace": NAMESPACE, "template_id": template_id,
        "template_version": version, "version": 1, "status": "active", "data": data,
        "term_references": [
            {"field_path": "unit", "term_id": term_id, "terminology_ref": lov, "value": "kg"},
        ],
    }


async def _seed_target(mongo) -> dict:
    await _target(mongo, {**_tpl("B-tpl", 1, [UNIT_B]), "identity_fields": ["unit"]})
    await _seed(mongo, "terminologies", [
        {"terminology_id": "B-lov", "namespace": NAMESPACE, "value": "UNIT", "label": "Unit"},
    ])
    await _seed(mongo, "terms", [
        {"term_id": "B-kg", "terminology_id": "B-lov", "namespace": NAMESPACE,
         "value": "kg", "label": "kg", "status": "active"},
    ])
    held = _hashed(_row("T-1", "B-tpl", 1, "B-kg", "B-lov", {"unit": "kg"}), [UNIT_B])
    await _seed(mongo, "documents", [held])
    return held


def _archive_with_note():
    archived = _hashed(
        _row("D-1", "A-tpl", 2, "A-kg", "A-lov", {"unit": "kg", "note": "only here"}),
        [UNIT_A, NOTE],
    )
    return archived, _archive({
        "terminologies": [
            {"terminology_id": "A-lov", "namespace": NAMESPACE, "value": "UNIT", "label": "Unit"},
        ],
        "terms": [
            {"term_id": "A-kg", "terminology_id": "A-lov", "namespace": NAMESPACE,
             "value": "kg", "label": "kg", "status": "active"},
        ],
        "templates": [{**_tpl("A-tpl", 2, [UNIT_A, NOTE]), "identity_fields": ["unit"]}],
        "documents": [archived],
    })


@pytest.mark.asyncio
async def test_a_document_held_under_its_rehashed_identity_does_not_decide_the_version(mongo):  # noqa: F811
    held = await _seed_target(mongo)
    archived, archive = _archive_with_note()
    # The premise: the archive's hash differs from the target's, and only the
    # term the identity resolves to tells them apart.
    assert archived["identity_hash"] != held["identity_hash"]

    events: list = []
    await _run_merge(mongo, archive, events, align_templates=True, on_clash="skip")

    report = _result(events)
    assert report["held_by_target_count"] == 1
    assert report["unplaceable_count"] == 0
    [version] = report["templates"][0]["versions"]
    assert version["target_version"] == 1
    [kept] = await _rows(mongo, "documents")
    assert (kept["document_id"], kept["data"]) == ("T-1", {"unit": "kg"})
