"""The `migrate` surgery: live migration semantics offline, with fills."""

import pytest
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.identity import compute_composite_key_hash, resolved_identity_values
from wip_archive.migrate import MigrationRefused, plan_migration
from wip_archive.model import ArchiveModel
from wip_archive.transform import run_transform

NS = "demo"
TPL = "0190c000-0000-7000-0000-0000000000t1"
LOV = "0190a000-0000-7000-0000-0000000000c1"
RED = "0190b000-0000-7000-0000-0000000000r1"
A, B = "019d0000-0000-7000-8000-00000000000a", "019d0000-0000-7000-8000-00000000000b"

V1_FIELDS = [
    {"name": "code", "type": "string", "mandatory": True},
    {"name": "hue", "type": "term", "terminology_ref": "COLOUR"},
    {"name": "legacy", "type": "string"},
]
V2_FIELDS = [
    {"name": "code", "type": "string", "mandatory": True},
    {"name": "colour", "type": "term", "terminology_ref": "COLOUR"},
    {"name": "legacy", "type": "string"},
    {"name": "region", "type": "string", "mandatory": True},
    {"name": "label", "type": "string"},
    {"name": "owner", "type": "reference", "reference_type": "document"},
]


def _template(version, fields, *, status="active", identity=("code",), versioned=True, renames=None):
    row = {"template_id": TPL, "version": version, "value": "ITEM", "namespace": NS,
           "status": status, "identity_fields": list(identity), "fields": fields,
           "versioned": versioned}
    if renames:
        row["renames"] = renames
    return row


def _doc(doc_id, code, *, status="active", legacy=None):
    data = {"code": code, "hue": "red"}
    if legacy:
        data["legacy"] = legacy
    terms = [{"field_path": "hue", "term_id": RED, "terminology_ref": LOV, "matched_via": "value"}]
    return {"document_id": doc_id, "namespace": NS, "template_id": TPL, "template_version": 1,
            "version": 1, "status": status, "data": data, "term_references": terms,
            "references": [], "file_references": [],
            "identity_hash": compute_composite_key_hash(
                resolved_identity_values(data, ["code"], V1_FIELDS, terms, [])),
            "metadata": {"warnings": [], "custom": {"src": "x"}},
            "created_at": "2026-01-01 00:00:00.000000", "created_by": "someone",
            "updated_at": "2026-01-01 00:00:00.000000", "updated_by": "someone"}


def _registry(entry_id, entity_type):
    return {"entry_id": entry_id, "namespace": NS, "entity_type": entity_type,
            "primary_composite_key": {}, "primary_composite_key_hash": "h", "synonyms": [],
            "status": "active"}


def _archive(tmp_path, v2=None, documents=None):
    rows = {
        "terminologies": [{"terminology_id": LOV, "value": "COLOUR", "namespace": NS,
                           "status": "active", "case_sensitive": False}],
        "terms": [{"term_id": RED, "terminology_id": LOV, "namespace": NS, "value": "red",
                   "status": "active", "names": []}],
        "templates": [_template(1, V1_FIELDS), v2 or _template(2, V2_FIELDS, renames={"colour": "hue"})],
        "documents": documents or [_doc(A, "A", legacy="L-1"), _doc(B, "B")],
        "registry_entries": [_registry(TPL, "templates"), _registry(LOV, "terminologies"),
                             _registry(RED, "terms"), _registry(A, "documents"),
                             _registry(B, "documents")],
    }
    path = tmp_path / "in.zip"
    writer = ArchiveWriter(path, default_namespace=NS)
    for entity_type, entities in rows.items():
        for entity in entities:
            writer.add_entity(entity_type, entity, namespace=NS)
    writer.write({"format_version": "3.1", "namespace": NS, "counts": {},
                  "namespaces": [{"prefix": NS, "counts": {}}],
                  "referents": writer.write_referents_file([])})
    return path


def _plan(path, mapping, **kwargs):
    with ArchiveReader(path) as reader:
        return plan_migration(reader, ArchiveModel.load(reader), mapping, **kwargs)


def _migrate(path, out, mapping, **kwargs):
    plan = _plan(path, mapping, **kwargs)
    result = run_transform(path, out, transform="migrate", toolkit_version="t", params=mapping,
                           keep_row=plan.keep_row, rewrite_row=plan.rewrite_row,
                           strict_identity=True, summary=plan.summary())
    with ArchiveReader(out) as reader:
        rows = {(d["document_id"], d["version"]): d
                for d in reader.read_entities("documents", namespace=NS)}
    return plan, result, rows


MAPPING = {"template": "ITEM", "to_version": 2, "fill": {"region": "EU"}}


def test_a_missing_mandatory_field_refuses_without_a_fill(tmp_path):
    with pytest.raises(MigrationRefused) as refused:
        _plan(_archive(tmp_path), {"template": "ITEM", "to_version": 2})
    assert "2 document(s) would not pass" in str(refused.value)
    assert refused.value.failures[0].endswith("region: required")


def test_a_versioned_migration_retires_the_old_row_and_writes_the_next(tmp_path):
    plan, result, rows = _migrate(_archive(tmp_path), tmp_path / "out.zip", MAPPING)
    assert result.verdict == "pass", [f.to_dict() for f in result.findings]
    old, new = rows[(A, 1)], rows[(A, 2)]
    assert old["status"] == "inactive" and old["updated_by"] == "wip-toolkit:migrate"
    assert old["data"] == {"code": "A", "hue": "red", "legacy": "L-1"}  # history untouched
    assert new["status"] == "active" and new["template_version"] == 2
    # The rename moved hue to colour, and its term snapshot with it.
    assert new["data"] == {"code": "A", "colour": "red", "legacy": "L-1", "region": "EU"}
    assert [t["field_path"] for t in new["term_references"]] == ["colour"]
    assert new["term_references"][0]["term_id"] == RED
    assert new["identity_hash"] == old["identity_hash"]
    assert new["created_at"] == old["created_at"] and new["created_by"] == "wip-toolkit:migrate"
    assert new["metadata"]["custom"] == {"src": "x"}
    assert plan.summary()["fields_filled"] == {"region": 2}
    assert result.hop["summary"]["documents_migrated"] == 2


def test_copy_and_format_rules(tmp_path):
    mapping = {"template": "ITEM", "to_version": 2,
               "fill": {"region": {"copy": "legacy"}, "label": {"format": "{code}/{region}"}}}
    # B has no legacy value, so its region stays empty and it fails.
    with pytest.raises(MigrationRefused):
        _plan(_archive(tmp_path), mapping)
    _p, _r, rows = _migrate(_archive(tmp_path), tmp_path / "o.zip", mapping,
                                     skip_failing=True)
    assert rows[(A, 2)]["data"]["region"] == "L-1"
    assert rows[(A, 2)]["data"]["label"] == "A/L-1"
    # Skipped: B stays on its version, untouched.
    assert (B, 2) not in rows and rows[(B, 1)]["status"] == "active"


def test_a_value_rule_writes_a_dict_that_looks_like_a_rule(tmp_path):
    mapping = {"template": "ITEM", "to_version": 2,
               "fill": {"region": "EU", "label": {"value": "{not a format}"}}}
    _p, _r, rows = _migrate(_archive(tmp_path), tmp_path / "o.zip", mapping)
    assert rows[(A, 2)]["data"]["label"] == "{not a format}"


def test_a_fill_never_overwrites_data(tmp_path):
    mapping = {"template": "ITEM", "to_version": 2, "fill": {"region": "EU", "legacy": "NEW"}}
    _p, _r, rows = _migrate(_archive(tmp_path), tmp_path / "o.zip", mapping)
    assert rows[(A, 2)]["data"]["legacy"] == "L-1" and rows[(B, 2)]["data"]["legacy"] == "NEW"


def test_versioned_false_overwrites_in_place(tmp_path):
    v2 = _template(2, V2_FIELDS, versioned=False, renames={"colour": "hue"})
    _p, result, rows = _migrate(_archive(tmp_path, v2=v2), tmp_path / "o.zip", MAPPING)
    assert sorted(rows) == [(A, 1), (B, 1)]
    assert rows[(A, 1)]["template_version"] == 2 and rows[(A, 1)]["status"] == "active"
    assert rows[(A, 1)]["created_by"] == "someone"  # in place: the creator stands
    assert result.verdict == "pass"


@pytest.mark.parametrize(("mapping", "message"), [
    ({"template": "ITEM", "to_version": 9}, "v9 is not in the archive"),
    ({"template": "NOPE", "to_version": 2}, "no template 'NOPE'"),
    ({"template": "ITEM", "to_version": 2, "fill": {"owner": "x"}}, "reference fields cannot"),
    ({"template": "ITEM", "to_version": 2, "fill": {"ghost": 1}}, "does not declare"),
    ({"template": "ITEM"}, "to_version"),
])
def test_refusals(tmp_path, mapping, message):
    with pytest.raises(MigrationRefused, match=message):
        _plan(_archive(tmp_path), mapping)


def test_an_inactive_target_and_an_identity_fork_are_refused(tmp_path):
    with pytest.raises(MigrationRefused, match="not active"):
        _plan(_archive(tmp_path, v2=_template(2, V2_FIELDS, status="inactive")), MAPPING)
    fork = tmp_path / "fork"
    fork.mkdir()
    with pytest.raises(MigrationRefused, match="fork"):
        _plan(_archive(fork, v2=_template(2, V2_FIELDS, identity=("code", "region"))), MAPPING)


def test_only_active_documents_migrate(tmp_path):
    path = _archive(tmp_path, documents=[_doc(A, "A"), _doc(B, "B", status="inactive")])
    assert _plan(path, MAPPING).cohort == {(A, 1)}
