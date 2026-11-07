"""Every identity mapping a merge discovers reaches its referrers (CASE-1095 R2).

When the target already held a document or a file under another id, the merge
learned that mapping only after the incoming documents had been rewritten:
documents were planned before files, and each document's mapping arrived after
the documents referencing it. A new document pointing at such an entity kept
the archive's id. Files are now planned first, and documents until a round
discovers no new mapping.
"""

from __future__ import annotations

import pytest

from tests.test_case_1095_template_version_id import mongo  # noqa: F401
from tests.test_merge_restore import (
    NAMESPACE,
    _archive,
    _document,
    _rows,
    _run_merge,
    _seed,
    _seed_namespace,
    _template,
)


def _file(file_id: str, checksum: str = "c1") -> dict:
    return {
        "file_id": file_id, "namespace": NAMESPACE, "checksum": checksum,
        "filename": "scan.png", "content_type": "image/png", "size_bytes": 10,
        "status": "active", "storage_key": f"{NAMESPACE}/{file_id}",
    }


def _referrer(doc_id: str, *, to_doc: str, to_file: str) -> dict:
    row = _document(1, doc_id=doc_id)
    row["identity_hash"] = "h-new"
    row["references"] = [{
        "field_path": "related", "reference_type": "document",
        "resolved": {"document_id": to_doc, "template_id": "TPL", "identity_hash": "h1"},
    }]
    row["file_references"] = [{"field_path": "scan", "file_id": to_file}]
    return row


@pytest.mark.asyncio
async def test_a_new_document_points_at_what_the_target_holds_under_other_ids(mongo):  # noqa: F811
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_template(1)])
    await _seed(mongo, "documents", [_document(1, doc_id="D-TGT")])
    await _seed(mongo, "files", [_file("F-TGT")])

    await _run_merge(mongo, _archive({
        "templates": [_template(1)],
        "documents": [
            _referrer("NEW", to_doc="D-ARC", to_file="F-ARC"),
            _document(1, doc_id="D-ARC"),
        ],
        "files": [_file("F-ARC")],
    }), on_clash="skip", skip_files=False)

    rows = {r["document_id"]: r for r in await _rows(mongo, "documents")}
    assert set(rows) == {"D-TGT", "NEW"}
    assert rows["NEW"]["references"][0]["resolved"]["document_id"] == "D-TGT"
    assert rows["NEW"]["file_references"][0]["file_id"] == "F-TGT"


def _ref_template() -> dict:
    """A template whose identity is a reference to another document."""
    return {
        "template_id": "TPL-REF", "namespace": NAMESPACE, "value": "LINK",
        "version": 1, "versioned": True, "label": "Link",
        "identity_fields": ["related"],
        "fields": [{"name": "related", "type": "reference", "reference_type": "document"}],
    }


def _link(doc_id: str, to_doc: str) -> dict:
    from wip_auth.document_identity import compute_hash

    return {
        "document_id": doc_id, "namespace": NAMESPACE, "template_id": "TPL-REF",
        "template_version": 1, "version": 1, "status": "active",
        "data": {"related": to_doc},
        "identity_hash": compute_hash({"related": to_doc}),
        "references": [{
            "field_path": "related", "reference_type": "document",
            "resolved": {"document_id": to_doc, "template_id": "TPL", "identity_hash": "h1"},
        }],
    }


@pytest.mark.asyncio
async def test_a_document_whose_identity_hashes_a_mapped_reference_matches_the_targets_copy(mongo):  # noqa: F811
    """A's identity is its reference to B. B matches the target under another
    id; only once that mapping is known does A hash to the target's copy of A.
    Planned once, A would be inserted as a second document."""
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_template(1), _ref_template()])
    await _seed(mongo, "documents", [_document(1, doc_id="B-TGT"), _link("A-TGT", "B-TGT")])

    await _run_merge(mongo, _archive({
        "templates": [_template(1), _ref_template()],
        "documents": [_link("A-ARC", "B-ARC"), _document(1, doc_id="B-ARC")],
    }), on_clash="skip")

    ids = sorted(r["document_id"] for r in await _rows(mongo, "documents"))
    assert ids == ["A-TGT", "B-TGT"]
