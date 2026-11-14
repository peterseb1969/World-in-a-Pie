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


# ---------------------------------------------------------------------------
# Identity: the stored hash must be the one the target produces (as live)
# ---------------------------------------------------------------------------

from wip_archive.identity import compute_composite_key_hash, resolved_identity_values  # noqa: E402

KIND = {"name": "kind", "type": "term", "terminology_ref": "wip:KIND", "mandatory": True}


def _hashed(doc, identity, fields, term_references=()):
    doc = dict(doc, term_references=list(term_references))
    doc["identity_hash"] = compute_composite_key_hash(resolved_identity_values(
        doc["data"], list(identity), fields, list(term_references), [],
    ))
    return doc


def test_a_stale_stored_hash_fails_the_document_as_live_does(tmp_path):
    healthy = _hashed(_doc("d1", {"code": "A"}), ["code"], [CODE])
    stale = dict(_doc("d2", {"code": "B"}), identity_hash="f" * 64)
    v2 = _template(2, [CODE, {"name": "name", "type": "string"}])
    report = _assess(tmp_path, [V1, v2], [healthy, stale])
    cohort = report.cohorts[1]
    assert cohort.ready == 1 and cohort.identity_changed == 1
    assert cohort.failures[(None, "identity_hash_stale")] == 1


def test_an_identity_term_from_an_uncarried_vocabulary_uses_the_stored_resolution(tmp_path):
    fields = [CODE, KIND]
    stored = [{"field_path": "kind", "term_id": "0190c000-0000-7000-0000-00000000kind",
               "terminology_ref": "wip:KIND", "value": "big"}]
    v1 = _template(1, fields, identity=("code", "kind"))
    v2 = _template(2, [*fields, {"name": "name", "type": "string"}], identity=("code", "kind"))
    doc = _hashed(_doc("d1", {"code": "A", "kind": "big"}), ["code", "kind"], fields, stored)

    report = _assess(tmp_path, [v1, v2], [doc])

    cohort = report.cohorts[1]
    assert cohort.ready == 1, cohort.to_dict()
    assert cohort.identity_changed == 0
    assert any(kind == "terminology" for kind, _ in cohort.unverifiable)


def test_a_missing_parent_is_reported_as_such_not_as_a_missing_version(tmp_path):
    v1 = dict(V1, extends="0190c000-0000-7000-0000-000000parent", extends_version=1)
    v2 = _template(2, [CODE, {"name": "name", "type": "string"}])
    report = _assess(tmp_path, [v1, v2], [_doc("d1", {"code": "A"})])
    blocked = report.cohorts[1].blocked
    assert "parent template" in blocked and "is not in the archive" in blocked
    assert not blocked.startswith("version 1 is not in the archive")


def test_an_unpinned_parent_is_reported_as_such_not_as_a_missing_version(tmp_path):
    """Provenance: CASE-1283. A source version the archive carries but cannot
    resolve (an unpinned parent, circular inheritance, too deep a chain)
    raises without ``missing``; that was reported as the version being absent."""
    v1 = dict(V1, extends="0190c000-0000-7000-0000-000000parent")
    v2 = _template(2, [CODE, {"name": "name", "type": "string"}])
    report = _assess(tmp_path, [v1, v2], [_doc("d1", {"code": "A"})])
    blocked = report.cohorts[1].blocked
    assert "without a pinned extends_version" in blocked
    assert not blocked.startswith("version 1 is not in the archive")
