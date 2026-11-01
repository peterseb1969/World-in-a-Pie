"""The document check behind `verify`: every document against its pinned
template version, its stored term references and its identity hash."""

from test_definitions import _archive_with_referents
from test_model import _registry, build_archive
from wip_archive.archive import ArchiveReader
from wip_archive.identity import compute_composite_key_hash, resolved_identity_values
from wip_archive.model import SCHEMA_VERSION, ArchiveModel

NS = "demo"
TPL = "0190c000-0000-7000-0000-0000000000t1"
LOV = "0190a000-0000-7000-0000-0000000000c1"
FOREIGN = "0190c000-0000-7000-0000-0000000000f1"

FIELDS = [
    {"name": "code", "type": "string", "mandatory": True},
    {"name": "colour", "type": "term", "terminology_ref": "COLOUR"},
    {"name": "count", "type": "integer", "validation": {"maximum": 10}},
]


def _template(version=1, fields=FIELDS):
    return {"template_id": TPL, "version": version, "value": "ITEM", "namespace": NS,
            "status": "active", "identity_fields": ["code"], "fields": fields}


def _terms():
    return [
        {"term_id": "t-red", "terminology_id": LOV, "namespace": NS, "value": "red",
         "status": "active", "names": []},
        {"term_id": "t-grey", "terminology_id": LOV, "namespace": NS, "value": "grey",
         "status": "deprecated", "names": []},
    ]


def _doc(document_id, data, *, template_id=TPL, template_version=1, term_refs=(), identity_hash=None):
    if identity_hash is None:
        identity_hash = compute_composite_key_hash(
            resolved_identity_values(data, ["code"], FIELDS, list(term_refs), [])
        )
    return {
        "document_id": document_id, "namespace": NS, "template_id": template_id,
        "template_version": template_version, "version": 1, "status": "active",
        "identity_hash": identity_hash, "data": data,
        "term_references": list(term_refs), "references": [], "file_references": [],
    }


def _rows(documents, templates=None):
    return {
        "terminologies": [{"terminology_id": LOV, "value": "COLOUR", "namespace": NS,
                           "case_sensitive": False, "status": "active"}],
        "terms": _terms(),
        "templates": templates or [_template()],
        "documents": documents,
        "registry_entries": [_registry(d["document_id"], "documents") for d in documents],
    }


def _check(path):
    with ArchiveReader(path) as reader:
        model = ArchiveModel.load(reader, check_documents=True)
    return model, {f.finding_class: f for f in model.findings()}


RED = [{"field_path": "colour", "term_id": "t-red"}]


def test_a_consistent_archive_has_no_document_findings(tmp_path):
    path = build_archive(tmp_path / "a.zip", _rows([
        _doc("d1", {"code": "A", "colour": "red", "count": 3}, term_refs=RED),
    ]))
    model, findings = _check(path)
    check = model.document_check
    assert check.checked == 1 and check.identity_checked == 1
    assert not {"document_invalid", "identity_hash_mismatch", "template_version_missing",
                "unverifiable_dependency"} & set(findings)


def test_a_document_failing_its_pinned_schema_is_a_warning(tmp_path):
    path = build_archive(tmp_path / "b.zip", _rows([
        _doc("d1", {"code": "A", "count": 99, "extra": 1}),
    ]))
    model, findings = _check(path)
    finding = findings["document_invalid"]
    assert finding.severity == "warning" and finding.count == 1
    assert finding.samples == ("d1 v1: count maximum",)
    codes = {(r["field"], r["code"]) for r in model.document_check.to_dict()["invalid_by_template"]}
    assert codes == {("count", "maximum"), ("extra", "unknown_field")}


def test_each_document_is_judged_against_its_own_pinned_version(tmp_path):
    v2 = _template(version=2, fields=[*FIELDS, {"name": "size", "type": "string", "mandatory": True}])
    path = build_archive(tmp_path / "c.zip", _rows(
        [_doc("d1", {"code": "A"}, template_version=1)], templates=[_template(), v2],
    ))
    _model, findings = _check(path)
    # v2's new mandatory field does not apply to a document pinned to v1.
    assert "document_invalid" not in findings


def test_a_missing_pinned_version_is_an_error(tmp_path):
    path = build_archive(tmp_path / "d.zip", _rows([
        _doc("d1", {"code": "A"}, template_version=3),
    ]))
    _model, findings = _check(path)
    assert findings["template_version_missing"].severity == "error"
    assert findings["template_version_missing"].samples == ("d1 v1: ITEM v3",)


def test_a_template_outside_the_archive_is_unverifiable(tmp_path):
    path = build_archive(tmp_path / "e.zip", _rows([
        _doc("d1", {"code": "A"}, template_id=FOREIGN),
    ]))
    model, findings = _check(path)
    assert findings["unverifiable_dependency"].severity == "warning"
    assert model.document_check.to_dict()["unverifiable"] == [
        {"kind": "template", "ref": FOREIGN, "documents": 1},
    ]


def test_a_wrong_identity_hash_is_a_warning_naming_the_rehash(tmp_path):
    path = build_archive(tmp_path / "f.zip", _rows([
        _doc("d1", {"code": "A"}, identity_hash="0" * 64),
    ]))
    _model, findings = _check(path)
    finding = findings["identity_hash_mismatch"]
    assert finding.severity == "warning" and "rehash-identities" in finding.detail


def test_a_retired_term_does_not_fail_existing_data(tmp_path):
    grey = [{"field_path": "colour", "term_id": "t-grey"}]
    path = build_archive(tmp_path / "g.zip", _rows([
        _doc("d1", {"code": "A", "colour": "grey"}, term_refs=grey),
    ]))
    _model, findings = _check(path)
    assert "document_invalid" not in findings
    assert "missing_term_reference" not in findings


def test_a_term_reference_found_nowhere_is_an_error_only_with_a_referents_table(tmp_path):
    lost = [{"field_path": "colour", "term_id": "t-lost"}]
    rows = _rows([_doc("d1", {"code": "A", "colour": "red"}, term_refs=lost)])
    with_table = _archive_with_referents(tmp_path / "h.zip", rows, [])
    _model, findings = _check(with_table)
    assert findings["missing_term_reference"].severity == "error"

    recorded = _archive_with_referents(tmp_path / "i.zip", rows, [
        {"entry_id": "t-lost", "namespace": "wip", "entity_type": "terms",
         "primary_composite_key": {}, "synonyms": []},
    ])
    _model, findings = _check(recorded)
    assert "missing_term_reference" not in findings

    # An archive older than the referents table cannot prove absence.
    without_table = build_archive(tmp_path / "j.zip", rows)
    _model, findings = _check(without_table)
    assert "missing_term_reference" not in findings


def test_json_carries_the_document_checks_only_when_run(tmp_path):
    path = build_archive(tmp_path / "k.zip", _rows([_doc("d1", {"code": "A"})]))
    with ArchiveReader(path) as reader:
        plain = ArchiveModel.load(reader).to_dict()
    assert SCHEMA_VERSION == "1.2"
    assert plain["schema"] == "1.2" and plain["document_checks"] is None
    model, _findings = _check(path)
    payload = model.to_dict()
    assert payload["document_checks"]["documents_checked"] == 1
    assert payload["document_checks"]["identity"]["checked"] == 1
