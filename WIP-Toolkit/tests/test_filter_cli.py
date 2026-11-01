"""`wip-toolkit filter`: the plan applied, verified and recorded, and every
way it refuses to write."""

import json

import pytest
from click.testing import CliRunner
from test_restore_cmd import _real_archive
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.identity import compute_composite_key_hash
from wip_toolkit.cli import _get_version, main

NS = "aa"
SPELL = "0190c000-0000-7000-0000-0000000000s1"
MONSTER = "0190c000-0000-7000-0000-0000000000m1"
S1 = "019d0000-0000-7000-8000-0000000000s1"
M1 = "019d0000-0000-7000-8000-0000000000m1"


def run(*args):
    return CliRunner().invoke(main, ["filter", *map(str, args)])


def _doc(doc_id, template_id, data, refs=()):
    return {"document_id": doc_id, "namespace": NS, "template_id": template_id,
            "template_version": 1, "version": 1, "status": "active",
            "identity_hash": compute_composite_key_hash({"name": data["name"]}),
            "data": data, "term_references": [], "references": list(refs),
            "file_references": []}


@pytest.fixture
def archive(tmp_path):
    name = {"name": "name", "type": "string", "mandatory": True}
    rows = {
        "templates": [
            {"template_id": SPELL, "version": 1, "value": "SPELL", "namespace": NS,
             "status": "active", "identity_fields": ["name"], "fields": [name]},
            {"template_id": MONSTER, "version": 1, "value": "MONSTER", "namespace": NS,
             "status": "active", "identity_fields": ["name"],
             "fields": [name, {"name": "favourite", "type": "reference",
                               "reference_type": "document", "target_templates": ["SPELL"]}]},
        ],
        "documents": [
            _doc(S1, SPELL, {"name": "Fireball"}),
            _doc(M1, MONSTER, {"name": "Smaug", "favourite": S1}, refs=[{
                "field_path": "favourite", "reference_type": "document",
                "resolved": {"document_id": S1, "namespace": NS, "template_id": SPELL}}]),
        ],
        "registry_entries": [
            {"entry_id": i, "namespace": NS, "entity_type": t, "primary_composite_key": {},
             "primary_composite_key_hash": "h", "synonyms": [], "status": "active"}
            for i, t in ((SPELL, "templates"), (MONSTER, "templates"),
                         (S1, "documents"), (M1, "documents"))
        ],
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


def _manifest(path):
    with ArchiveReader(path) as reader:
        return reader.read_manifest_raw()


def test_drop_writes_a_verified_archive_and_records_the_hop(archive, tmp_path):
    out = tmp_path / "out.zip"
    result = run(archive, out, "MONSTER")
    assert result.exit_code == 0, result.output
    assert "1 reference(s) cross to 1 document(s) left out" in result.output
    assert "verdict: pass" in result.output
    [hop] = _manifest(out)["derived_from"]
    assert hop["transform"] == "filter"
    assert hop["toolkit_version"] == _get_version()
    assert hop["params"] == {"templates": ["MONSTER"], "boundary": "drop",
                             "relationships": True, "definitions_only": False}
    assert hop["summary"]["cut_documents"] == 1
    with ArchiveReader(out) as reader:
        assert [r["entry_id"] for r in reader.read_referents()] == [S1]


def test_json_carries_the_hop_and_verdict(archive, tmp_path):
    result = run(archive, tmp_path / "out.zip", "MONSTER", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "1.1"
    assert payload["dry_run"] is False
    assert payload["verdict"] == "pass"
    assert payload["hop"]["transform"] == "filter"


def test_strict_refuses_and_writes_nothing(archive, tmp_path):
    out = tmp_path / "out.zip"
    result = run(archive, out, "MONSTER", "--boundary", "strict")
    assert result.exit_code == 1
    assert "Refused (--boundary strict)" in result.output
    assert not out.exists()


def test_closure_over_half_the_archive_needs_yes(archive, tmp_path):
    out = tmp_path / "out.zip"
    refused = run(archive, out, "MONSTER", "--boundary", "closure")
    assert refused.exit_code == 1
    assert "keeps 2 of the archive's 2 documents" in refused.output
    assert not out.exists()
    accepted = run(archive, out, "MONSTER", "--boundary", "closure", "--yes")
    assert accepted.exit_code == 0, accepted.output
    assert _manifest(out)["derived_from"][0]["summary"]["cut_documents"] == 0


def test_an_existing_output_needs_force(archive, tmp_path):
    out = tmp_path / "out.zip"
    out.write_bytes(b"keep me")
    refused = run(archive, out, "MONSTER")
    assert refused.exit_code == 1 and "--force" in refused.output
    assert out.read_bytes() == b"keep me"
    assert run(archive, out, "MONSTER", "--force").exit_code == 0


def test_an_unknown_template_is_refused(archive, tmp_path):
    result = run(archive, tmp_path / "out.zip", "DRAGON")
    assert result.exit_code == 1 and "no template 'DRAGON'" in result.output


def test_definitions_only_carries_no_document(archive, tmp_path):
    out = tmp_path / "out.zip"
    result = run(archive, out, "--definitions-only")
    assert result.exit_code == 0, result.output
    with ArchiveReader(out) as reader:
        assert list(reader.read_entities("documents", namespace=NS)) == []
        assert len(list(reader.read_entities("templates", namespace=NS))) == 2


def test_a_failed_verdict_still_writes_and_exits_1(tmp_path):
    broken = _real_archive(tmp_path, template_version=3)
    out = tmp_path / "out.zip"
    result = run(broken, out, "ITEM")
    assert result.exit_code == 1
    assert "verdict: fail" in result.output
    assert _manifest(out)["derived_from"][-1]["verify"]["verdict"] == "fail"


def test_dry_run_writes_nothing_and_reports_the_plan(archive, tmp_path):
    out = tmp_path / "out.zip"
    result = run(archive, out, "MONSTER", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "Nothing was written" in result.output
    assert "verdict: pass" in result.output
    assert not out.exists()


def test_dry_run_json_says_so(archive, tmp_path):
    result = run(archive, tmp_path / "out.zip", "MONSTER", "--dry-run", "--json")
    payload = json.loads(result.output)
    assert payload["dry_run"] is True and payload["output"] is None
    assert payload["hop"]["summary"]["cut_documents"] == 1


def test_a_closure_dry_run_needs_no_yes(archive, tmp_path):
    out = tmp_path / "out.zip"
    result = run(archive, out, "MONSTER", "--boundary", "closure", "--dry-run")
    assert result.exit_code == 0, result.output
    assert not out.exists()


def test_strict_still_refuses_in_a_dry_run(archive, tmp_path):
    result = run(archive, tmp_path / "out.zip", "MONSTER", "--boundary", "strict", "--dry-run")
    assert result.exit_code == 1 and "Refused" in result.output
