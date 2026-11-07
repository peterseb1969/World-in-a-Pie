"""The `redact` surgery: named fields cleared in every version, snapshots and
unshared files with them, identity refused, values never recorded."""

import pytest
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.identity import compute_composite_key_hash, resolved_identity_values
from wip_archive.model import ArchiveModel
from wip_archive.redact import RedactionRefused, plan_redaction
from wip_archive.transform import run_transform

NS = "demo"
PERSON, NOTE = "0190c000-0000-7000-0000-0000000000p1", "0190c000-0000-7000-0000-0000000000n1"
CONTACT = "0190c000-0000-7000-0000-0000000000c1"
P1, N1 = "019d0000-0000-7000-8000-0000000000p1", "019d0000-0000-7000-8000-0000000000n1"
F_ONLY, F_SHARED = "019f0000-0000-7000-8000-0000000000f1", "019f0000-0000-7000-8000-0000000000f2"
FIELDS = [
    {"name": "code", "type": "string", "mandatory": True},
    {"name": "email", "type": "string"},
    {"name": "contact", "type": "object"},
    {"name": "mood", "type": "term", "terminology_ref": "MOOD"},
    {"name": "scans", "type": "array", "array_item_type": "file"},
    {"name": "contacts", "type": "array", "array_item_type": "object", "array_template_ref": "CONTACT"},
    {"name": "tags", "type": "array", "array_item_type": "term", "terminology_ref": "TAG"},
    {"name": "address", "type": "object", "template_ref": "ADDRESS"},  # not in the archive
    {"name": "remark", "type": "string"},  # never filled
]
CONTACT_FIELDS = [
    {"name": "email", "type": "string"},
    {"name": "country", "type": "term", "terminology_ref": "COUNTRY"},
]


def _person(version, status):
    data = {"code": "P", "email": f"p{version}@example.org",
            "contact": {"city": "Bern", "phone": "079"}, "mood": "calm",
            "scans": [F_ONLY, F_SHARED],
            "contacts": [{"email": f"a{version}@example.org", "country": "CH"},
                         {"email": f"b{version}@example.org"}],
            "tags": ["x", "y"], "remark": None}
    return {"document_id": P1, "namespace": NS, "template_id": PERSON, "template_version": 1,
            "version": version, "status": status, "data": data,
            "identity_hash": compute_composite_key_hash(
                resolved_identity_values(data, ["code"], FIELDS, [], [])),
            "term_references": [{"field_path": "mood", "term_id": "t-calm"},
                                {"field_path": "contacts[0].country", "term_id": "t-ch"},
                                {"field_path": "tags[0]", "term_id": "t-x"},
                                {"field_path": "tags[1]", "term_id": "t-y"}],
            "metadata": {"warnings": ["Value 'calm' in field 'mood' is ambiguous"]},
            "references": [],
            "file_references": [{"field_path": "scans[0]", "file_id": F_ONLY},
                                {"field_path": "scans[1]", "file_id": F_SHARED}],
            "updated_at": "2026-01-01 00:00:00.000000", "updated_by": "someone"}


def _archive(tmp_path):
    note = {"document_id": N1, "namespace": NS, "template_id": NOTE, "template_version": 1,
            "version": 1, "status": "active", "data": {"scan": F_SHARED}, "identity_hash": "",
            "term_references": [], "references": [],
            "file_references": [{"field_path": "scan", "file_id": F_SHARED}]}
    rows = {
        "templates": [
            {"template_id": PERSON, "version": 1, "value": "PERSON", "namespace": NS,
             "status": "active", "identity_fields": ["code"], "fields": FIELDS},
            {"template_id": NOTE, "version": 1, "value": "NOTE", "namespace": NS,
             "status": "active", "identity_fields": [], "fields": [{"name": "scan", "type": "file"}]},
            {"template_id": CONTACT, "version": 1, "value": "CONTACT", "namespace": NS,
             "status": "active", "identity_fields": [], "fields": CONTACT_FIELDS},
        ],
        "documents": [_person(1, "inactive"), _person(2, "active"), note],
        "files": [{"file_id": f, "namespace": NS, "status": "active"} for f in (F_ONLY, F_SHARED)],
        "registry_entries": [
            {"entry_id": i, "namespace": NS, "entity_type": t, "primary_composite_key": {},
             "primary_composite_key_hash": "h", "synonyms": [], "status": "active"}
            for i, t in ((PERSON, "templates"), (NOTE, "templates"), (CONTACT, "templates"),
                         (P1, "documents"),
                         (N1, "documents"), (F_ONLY, "files"), (F_SHARED, "files"))],
    }
    path = tmp_path / "in.zip"
    writer = ArchiveWriter(path, default_namespace=NS)
    for entity_type, entities in rows.items():
        for entity in entities:
            writer.add_entity(entity_type, entity, namespace=NS)
    for f in (F_ONLY, F_SHARED):
        writer.add_blob(f, b"scan")
    writer.write({"format_version": "3.1", "namespace": NS, "include_files": True, "counts": {},
                  "namespaces": [{"prefix": NS, "counts": {}}],
                  # The terms the snapshots name live outside the archive.
                  "referents": writer.write_referents_file(
                      [{"entry_id": t, "entity_type": "terms", "namespace": "wip"}
                       for t in ("t-calm", "t-ch", "t-x", "t-y")])})
    return path


def _redact(path, out, spec):
    with ArchiveReader(path) as reader:
        plan = plan_redaction(reader, ArchiveModel.load(reader), spec)
    result = run_transform(path, out, transform="redact", toolkit_version="t",
                           params=plan.params(), params_redacted=True, keep_row=plan.keep_row,
                           rewrite_row=plan.rewrite_row, strict_identity=True,
                           summary=plan.summary())
    return plan, result


def _docs(path):
    with ArchiveReader(path) as reader:
        return {(d["document_id"], d["version"]): d
                for d in reader.read_entities("documents", namespace=NS)}


def test_every_version_is_cleared_and_the_hop_holds_no_values(tmp_path):
    out = tmp_path / "out.zip"
    plan, result = _redact(_archive(tmp_path), out, {"PERSON": ["email", "contact.phone", "mood"]})
    assert result.verdict == "pass", [f.to_dict() for f in result.findings]
    for version in (1, 2):
        row = _docs(out)[(P1, version)]
        assert row["data"]["email"] is None and row["data"]["mood"] is None
        assert row["data"]["contact"] == {"city": "Bern", "phone": None}
        # mood's snapshot went with it; the other fields' stay
        assert [t["field_path"] for t in row["term_references"]] == ["contacts[0].country", "tags[0]", "tags[1]"]
        assert row["updated_by"] == "someone"  # history rewritten in place, not re-signed
    assert plan.summary() == {"rows_redacted": 2, "values_cleared": 6, "files_dropped": 0}
    hop = result.hop
    assert hop["params_redacted"] is True
    assert hop["params"] == {"fields": {f"{NS}:PERSON": ["email", "contact.phone", "mood"]}}
    assert "example.org" not in str(hop)


def test_a_file_only_the_redacted_field_used_leaves_and_a_shared_one_stays(tmp_path):
    out = tmp_path / "out.zip"
    plan, _result = _redact(_archive(tmp_path), out, {"PERSON": ["scans"]})
    assert plan.files_dropped == {F_ONLY}
    with ArchiveReader(out) as reader:
        files = {f["file_id"] for f in reader.read_entities("files", namespace=NS)}
        assert files == {F_SHARED}
        assert reader.list_blobs() == [F_SHARED]
    assert _docs(out)[(P1, 2)]["file_references"] == []
    assert _docs(out)[(N1, 1)]["file_references"] == [{"field_path": "scan", "file_id": F_SHARED}]


def test_other_templates_are_untouched(tmp_path):
    out = tmp_path / "out.zip"
    src = _archive(tmp_path)
    _redact(src, out, {"PERSON": ["email"]})
    assert _docs(out)[(N1, 1)] == _docs(src)[(N1, 1)]


def test_a_path_through_an_array_clears_every_element_and_its_snapshots(tmp_path):
    """CASE-1107: a dot path into an array of objects clears the field in
    every element, and the snapshots under every element path go with it."""
    out = tmp_path / "out.zip"
    plan, result = _redact(_archive(tmp_path), out, {"PERSON": ["contacts.email", "contacts.country"]})
    assert result.verdict == "pass", [f.to_dict() for f in result.findings]
    for version in (1, 2):
        row = _docs(out)[(P1, version)]
        assert row["data"]["contacts"] == [{"email": None, "country": None}, {"email": None}]
        assert [t["field_path"] for t in row["term_references"]] == ["mood", "tags[0]", "tags[1]"]
    assert plan.summary()["values_cleared"] == 6  # two emails and a country, twice
    assert "example.org" not in str(result.hop)


def test_an_array_leaf_is_cleared_whole_with_its_element_snapshots(tmp_path):
    out = tmp_path / "out.zip"
    _redact(_archive(tmp_path), out, {"PERSON": ["tags"]})
    row = _docs(out)[(P1, 2)]
    assert row["data"]["tags"] is None
    assert [t["field_path"] for t in row["term_references"]] == ["mood", "contacts[0].country"]


def test_a_redacted_row_drops_its_warnings_and_others_keep_theirs(tmp_path):
    out = tmp_path / "out.zip"
    src = _archive(tmp_path)
    _redact(src, out, {"PERSON": ["mood"]})
    assert _docs(out)[(P1, 2)]["metadata"]["warnings"] == []
    assert _docs(out)[(N1, 1)] == _docs(src)[(N1, 1)]


@pytest.mark.parametrize(("spec", "message"), [
    ({"PERSON": ["code"]}, "identity field"),
    ({"PERSON": ["emial"]}, "declares no field 'emial'"),
    ({"PERSON": ["contacts.emial"]}, "PERSON.contacts declares no field 'emial'"),
    ({"PERSON": ["contacts[0].email"]}, "without an index"),
    ({"PERSON": ["email.domain"]}, "PERSON.email has no fields"),
    ({"PERSON": ["address.city"]}, "'ADDRESS', which is not in this archive"),
    ({"PERSON": ["remark"]}, "PERSON.remark has no value in any document"),
    ({"PERSON": ["contact.fax"]}, "contact.fax has no value"),
    ({"PERSON": ["contacts..email"]}, "empty path segment"),
    ({"NOPE": ["x"]}, "no template 'NOPE'"),
    ({}, "at least one"),
])
def test_refusals(tmp_path, spec, message):
    with ArchiveReader(_archive(tmp_path)) as reader, pytest.raises(RedactionRefused, match=message):
        plan_redaction(reader, ArchiveModel.load(reader), spec)
