"""DocumentTransformer.transform, pinned before its refactor.

Provenance: CASE-1161. The suite never reached these branches: a term or
file reference with no field path (or no term id) is ignored; a second
indexed file reference on one field joins the first; metadata columns are
left out when include_metadata is off; a top-level term reference whose
field the data does not carry still gets its <field>_term_id column; a file
reference for a field this template version does not declare keeps the
shape-based columns (list → one JSON column, single → three columns); and a
full_text_indexed flag on a non-string field writes no search column.
"""

import json

from reporting_sync.models import ReportingConfig
from reporting_sync.transformer import DocumentTransformer


def _document(**extra):
    doc = {
        "document_id": "doc-1",
        "namespace": "wip",
        "template_id": "tpl-1",
        "template_version": 2,
        "version": 3,
        "status": "active",
        "identity_hash": "h",
        "created_at": "2026-01-01T00:00:00Z",
        "created_by": "u",
        "data": {"title": "Hello", "count": 4},
    }
    doc.update(extra)
    return doc


TEMPLATE = {
    "fields": [
        {"name": "title", "type": "string"},
        {"name": "count", "type": "integer", "full_text_indexed": True},
        {"name": "body", "type": "string", "full_text_indexed": True},
    ],
}


def test_references_without_a_path_are_ignored_and_undeclared_fields_keep_shape_columns():
    doc = _document(
        term_references=[
            {"field_path": "", "term_id": "t0"},
            {"field_path": "colour", "term_id": ""},
            {"field_path": "kind", "term_id": "t-kind"},
        ],
        file_references=[
            {"field_path": "", "file_id": "f0"},
            {"field_path": "scans[0]", "file_id": "f1", "filename": "a.png", "content_type": "image/png"},
            {"field_path": "scans[1]", "file_id": "f2", "filename": "b.png", "content_type": "image/png"},
            {"field_path": "cover", "file_id": "f3", "filename": "c.pdf", "content_type": "application/pdf"},
        ],
    )
    rows = DocumentTransformer().transform(doc, TEMPLATE)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind_term_id"] == "t-kind"
    assert "colour_term_id" not in row
    assert [r["file_id"] for r in json.loads(row["scans"])] == ["f1", "f2"]
    assert (row["cover_file_id"], row["cover_filename"], row["cover_content_type"]) == (
        "f3", "c.pdf", "application/pdf",
    )
    assert "count_search" not in row
    assert row["body_search"] is None
    assert row["created_by"] == "u"
    assert row["template_version"] == 2 and row["version"] == 3


def test_metadata_columns_are_left_out_when_not_included():
    rows = DocumentTransformer(ReportingConfig(include_metadata=False)).transform(_document(), TEMPLATE)
    row = rows[0]
    for col in ("created_at", "created_by", "updated_at", "updated_by"):
        assert col not in row
    assert row["title"] == "Hello" and row["document_id"] == "doc-1"
