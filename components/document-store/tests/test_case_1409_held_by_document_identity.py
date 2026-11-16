"""Under align_templates and on_clash=skip, a document whose identity names
another archived document is recognised as held by the id that document has
on the target.

VISIT's identity is its `patient` reference. The archive's visit names the
archive's patient P-A; the target holds the same patient as P-B, and its own
copy of the visit names P-B. Which archived patient is the target's P-B is
known only once pass 2 has matched documents: the held-document probe used to
run before that, hashed the visit with P-A, missed the target's copy, counted
its row, and the `note` only that row fills left its version unplaced, which
left the visit out as unplaceable. Alignment now settles after documents are
matched, so the visit is held, its row pins nothing, and nothing is left out.
"""

from __future__ import annotations

import copy

import pytest

from document_store.services.remap_restore import rehash_document_row
from tests.test_case_1043_align_templates import (  # noqa: F401  (mongo is a fixture)
    _result,
    mongo,
)
from tests.test_merge_restore import NAMESPACE, _archive, _rows, _run_merge, _seed, _seed_namespace

CODE = {"name": "code", "type": "string"}
NOTE = {"name": "note", "type": "string"}


def _patient_field(template_id: str) -> dict:
    return {"name": "patient", "type": "reference", "reference_type": "document",
            "target_templates": [template_id]}


def _tpl(template_id: str, value: str, version: int, fields: list[dict], identity: list[str]) -> dict:
    return {
        "template_id": template_id, "namespace": NAMESPACE, "value": value, "label": value,
        "version": version, "status": "active", "identity_fields": identity, "fields": fields,
    }


def _hashed(row: dict, identity: list[str], fields: list[dict]) -> dict:
    row = copy.deepcopy(row)
    row["identity_hash"] = ""
    rehash_document_row(row, identity, fields)
    return row


def _patient(doc_id: str, template_id: str) -> dict:
    return _hashed({
        "document_id": doc_id, "namespace": NAMESPACE, "template_id": template_id,
        "template_version": 1, "version": 1, "status": "active", "data": {"code": "p1"},
    }, ["code"], [CODE])


def _visit(doc_id: str, template_id: str, version: int, patient: str, pat_tpl: str,
           data: dict, fields: list[dict]) -> dict:
    return _hashed({
        "document_id": doc_id, "namespace": NAMESPACE, "template_id": template_id,
        "template_version": version, "version": 1, "status": "active",
        "data": {"patient": patient, **data},
        "references": [{
            "field_path": "patient", "reference_type": "document",
            "lookup_value": patient,
            "resolved": {"document_id": patient, "template_id": pat_tpl, "namespace": NAMESPACE},
        }],
    }, ["patient"], fields)


@pytest.mark.asyncio
async def test_a_document_held_through_another_documents_identity_is_not_left_out(mongo):  # noqa: F811
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [
        _tpl("B-pat", "PATIENT", 1, [CODE], ["code"]),
        _tpl("B-vis", "VISIT", 1, [_patient_field("B-pat")], ["patient"]),
    ])
    target_visit = _visit("V-B", "B-vis", 1, "P-B", "B-pat", {}, [_patient_field("B-pat")])
    await _seed(mongo, "documents", [_patient("P-B", "B-pat"), target_visit])

    archive_fields = [_patient_field("A-pat"), NOTE]
    archived_visit = _visit(
        "V-A", "A-vis", 2, "P-A", "A-pat", {"note": "only here"}, archive_fields,
    )
    # The premise: the two visits hash differently, by the patient id alone.
    assert archived_visit["identity_hash"] != target_visit["identity_hash"]
    archive = _archive({
        "templates": [
            _tpl("A-pat", "PATIENT", 1, [CODE], ["code"]),
            _tpl("A-vis", "VISIT", 2, archive_fields, ["patient"]),
        ],
        "documents": [_patient("P-A", "A-pat"), archived_visit],
    })

    events: list = []
    await _run_merge(mongo, archive, events, align_templates=True, on_clash="skip")

    report = _result(events)
    assert report["unplaceable_count"] == 0, report
    assert report["held_by_target_count"] == 2
    visit = next(t for t in report["templates"] if t["value"] == "VISIT")
    [version] = visit["versions"]
    assert version["history"] and version["target_version"] is None
    assert version["outcome"] == "held" and version["held_rows"] == 1
    kept = {row["document_id"] for row in await _rows(mongo, "documents")}
    assert kept == {"P-B", "V-B"}
