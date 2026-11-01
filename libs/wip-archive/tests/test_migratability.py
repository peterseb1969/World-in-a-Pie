"""Migratability on hand-built archives: the refusals and the cohort rules.

Agreement with the live migrate dry run is proven by the toolkit's
integration suite (test_offline_migratability_parity.py).
"""

from test_model import build_archive
from wip_archive.archive import ArchiveReader
from wip_archive.definitions import DefinitionIndex
from wip_archive.migratability import assess_migration

TPL = "0190c000-0000-7000-0000-0000000000t1"
NS = "demo"


def _template(version, fields, *, identity=("code",), status="active", renames=None):
    row = {"template_id": TPL, "version": version, "value": "ITEM", "namespace": NS,
           "status": status, "identity_fields": list(identity), "fields": fields}
    if renames:
        row["renames"] = renames
    return row


CODE = {"name": "code", "type": "string", "mandatory": True}
V1 = _template(1, [CODE, {"name": "name", "type": "string"}])


def _doc(document_id, data, *, template_version=1, status="active"):
    return {"document_id": document_id, "namespace": NS, "template_id": TPL,
            "template_version": template_version, "version": 1, "status": status,
            "identity_hash": "", "data": data, "term_references": [], "references": []}


def _assess(tmp_path, templates, documents, to_version=2):
    path = build_archive(tmp_path / "m.zip", {"templates": templates, "documents": documents})
    with ArchiveReader(path) as reader:
        return assess_migration(reader, DefinitionIndex.load(reader), TPL, to_version)


def test_a_missing_target_refuses_the_migration(tmp_path):
    report = _assess(tmp_path, [V1], [_doc("d1", {"code": "A"})], to_version=7)
    assert report.blocked == "version 7 is not in the archive"


def test_an_inactive_target_refuses_the_migration(tmp_path):
    v2 = _template(2, [CODE], status="inactive")
    report = _assess(tmp_path, [V1, v2], [_doc("d1", {"code": "A"})])
    assert "not active" in report.blocked


def test_changed_identity_fields_refuse_the_cohort_as_a_fork(tmp_path):
    v2 = _template(2, [CODE, {"name": "name", "type": "string"}], identity=("code", "name"))
    report = _assess(tmp_path, [V1, v2], [_doc("d1", {"code": "A"}), _doc("d2", {"code": "B"})])
    cohort = report.cohorts[1]
    assert "fork" in cohort.blocked and cohort.documents == 2
    assert cohort.to_dict()["failing"] == 2


def test_renames_rekey_before_validation(tmp_path):
    v2 = _template(2, [CODE, {"name": "title", "type": "string", "mandatory": True}],
                   renames={"title": "name"})
    report = _assess(tmp_path, [V1, v2], [
        _doc("d1", {"code": "A", "name": "x"}),
        _doc("d2", {"code": "B"}),
    ])
    cohort = report.cohorts[1]
    assert cohort.ready == 1
    assert dict(cohort.failures) == {("title", "required"): 1}
    assert cohort.samples == ["d2 v1: title required"]


def test_only_active_documents_on_other_versions_are_the_cohort(tmp_path):
    v2 = _template(2, [CODE, {"name": "name", "type": "string"}])
    report = _assess(tmp_path, [V1, v2], [
        _doc("d1", {"code": "A"}),
        _doc("d2", {"code": "B"}, status="inactive"),
        _doc("d3", {"code": "C"}, template_version=2),
    ])
    assert set(report.cohorts) == {1}
    assert report.cohorts[1].documents == 1 and report.cohorts[1].ready == 1


def test_each_source_version_is_its_own_cohort(tmp_path):
    v2 = _template(2, [CODE, {"name": "name", "type": "string"}])
    v3 = _template(3, [CODE])
    report = _assess(tmp_path, [V1, v2, v3], [
        _doc("d1", {"code": "A", "name": "x"}),
        _doc("d2", {"code": "B", "name": "y"}, template_version=2),
    ], to_version=3)
    # "name" is gone in v3: both cohorts fail on unknown_field.
    assert {v: dict(c.failures) for v, c in report.cohorts.items()} == {
        1: {("name", "unknown_field"): 1},
        2: {("name", "unknown_field"): 1},
    }
    assert report.to_dict()["references_checked"] is False
