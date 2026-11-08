"""References through template- and terminology-typed fields keep their
targets: prune does not drop them, filter carries them, inspect's data
closure lists them. A document naming a template or a terminology needs its
definition, as a document holding a term needs the term's terminology."""

import pytest
from test_filter import _id, _registry, _write
from wip_archive.archive import ArchiveReader
from wip_archive.filter import plan_filter
from wip_archive.model import ArchiveModel
from wip_archive.prune import plan_prune

NS = "demo"
HOLDER, NAMED, DECLARER, UNUSED = _id("c", 31), _id("c", 32), _id("c", 33), _id("c", 34)
VOCAB, TERM = _id("a", 31), _id("b", 31)
DOC = _id("d", 31)


def _tpl(template_id, value, fields):
    return {"template_id": template_id, "version": 1, "value": value, "namespace": NS,
            "status": "active", "identity_fields": [], "fields": fields}


@pytest.fixture
def archive(tmp_path):
    rows = {
        "terminologies": [{"terminology_id": VOCAB, "value": "VOCAB", "namespace": NS,
                           "status": "active"}],
        "terms": [{"term_id": TERM, "terminology_id": VOCAB, "namespace": NS,
                   "value": "v", "status": "active", "names": []}],
        "templates": [
            # Holds the document; its fields name a template and a terminology.
            _tpl(HOLDER, "HOLDER", [
                {"name": "kind", "type": "reference", "reference_type": "template"},
                {"name": "vocabulary", "type": "reference", "reference_type": "terminology"},
            ]),
            # No documents, declared by nobody: only the document names it.
            _tpl(NAMED, "NAMED", [{"name": "x", "type": "string"}]),
            # No documents; the only template declaring VOCAB, so VOCAB is a
            # prune candidate unless something kept uses it.
            _tpl(DECLARER, "DECLARER", [{"name": "v", "type": "term", "terminology_ref": "VOCAB"}]),
            # The control: nothing names or needs it.
            _tpl(UNUSED, "UNUSED", [{"name": "y", "type": "string"}]),
        ],
        "documents": [{
            "document_id": DOC, "namespace": NS, "template_id": HOLDER, "template_version": 1,
            "version": 1, "status": "active", "identity_hash": "",
            "data": {"kind": "NAMED", "vocabulary": "VOCAB"},
            "term_references": [], "file_references": [],
            "references": [
                {"field_path": "kind", "reference_type": "template",
                 "resolved": {"template_id": NAMED, "namespace": NS}},
                {"field_path": "vocabulary", "reference_type": "terminology",
                 "resolved": {"terminology_id": VOCAB, "namespace": NS}},
            ],
        }],
        "registry_entries": [
            _registry(VOCAB, "terminologies"), _registry(TERM, "terms"),
            *(_registry(i, "templates") for i in (HOLDER, NAMED, DECLARER, UNUSED)),
            _registry(DOC, "documents"),
        ],
    }
    return _write(tmp_path / "in.zip", {NS: rows})


def test_prune_keeps_what_a_document_names(archive):
    with ArchiveReader(archive) as reader:
        plan = plan_prune(reader, ArchiveModel.load(reader))
    assert NAMED not in plan.templates
    assert VOCAB not in plan.terminologies
    # Unrelated unused templates still go.
    assert {DECLARER, UNUSED} <= plan.templates


def test_filter_carries_what_a_kept_document_names(archive):
    with ArchiveReader(archive) as reader:
        plan = plan_filter(reader, ArchiveModel.load(reader), ["HOLDER"])
    assert DOC in plan.documents
    assert NAMED in plan.templates
    assert VOCAB in plan.terminologies and TERM in plan.terms
    assert UNUSED not in plan.templates and DECLARER not in plan.templates


def test_filter_strict_has_nothing_to_refuse(archive):
    # Named definitions travel; the boundary policy covers documents only.
    with ArchiveReader(archive) as reader:
        plan = plan_filter(reader, ArchiveModel.load(reader), ["HOLDER"], boundary="strict")
    assert NAMED in plan.templates and plan.cut_references == 0


def test_the_data_closure_lists_what_documents_name(archive):
    with ArchiveReader(archive) as reader:
        model = ArchiveModel.load(reader)
    closure = model.data_closure({HOLDER})
    assert NAMED in closure.template_ids
    assert VOCAB in closure.terminology_ids
    assert UNUSED not in closure.template_ids
