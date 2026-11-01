"""`wip-toolkit latest-only`: newest versions travel, the hop counts the rest."""

import json

from click.testing import CliRunner
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_toolkit.cli import main

NS = "aa"
TPL = "0190c000-0000-7000-0000-0000000000t1"
DOC = "019d0000-0000-7000-8000-0000000000d1"


def run(*args):
    return CliRunner().invoke(main, ["latest-only", *map(str, args)])


def _archive(tmp_path):
    path = tmp_path / "in.zip"
    writer = ArchiveWriter(path, default_namespace=NS)
    writer.add_entity("templates", {"template_id": TPL, "version": 1, "value": "ITEM",
                                    "namespace": NS, "status": "active", "identity_fields": [],
                                    "fields": [{"name": "n", "type": "integer"}]}, namespace=NS)
    for version in (1, 2, 3):
        writer.add_entity("documents", {
            "document_id": DOC, "namespace": NS, "template_id": TPL, "template_version": 1,
            "version": version, "status": "active" if version == 3 else "inactive",
            "identity_hash": "", "data": {"n": version}, "term_references": [],
            "references": [], "file_references": []}, namespace=NS)
    for entry_id, entity_type in ((TPL, "templates"), (DOC, "documents")):
        writer.add_entity("registry_entries", {
            "entry_id": entry_id, "namespace": NS, "entity_type": entity_type,
            "primary_composite_key": {}, "primary_composite_key_hash": "h", "synonyms": [],
            "status": "active"}, namespace=NS)
    writer.write({"format_version": "3.1", "namespace": NS, "include_all_versions": True,
                  "counts": {}, "namespaces": [{"prefix": NS, "counts": {}}],
                  "referents": writer.write_referents_file([])})
    return path


def test_only_the_newest_version_travels(tmp_path):
    out = tmp_path / "out.zip"
    result = run(_archive(tmp_path), out)
    assert result.exit_code == 0, result.output
    assert "2 older version row(s) dropped; 0 kept" in result.output
    with ArchiveReader(out) as reader:
        rows = list(reader.read_entities("documents", namespace=NS))
        manifest = reader.read_manifest_raw()
    assert [(r["version"], r["data"]) for r in rows] == [(3, {"n": 3})]
    assert manifest["include_all_versions"] is False
    assert manifest["derived_from"][-1]["transform"] == "latest-only"


def test_json_and_force(tmp_path):
    src = _archive(tmp_path)
    out = tmp_path / "out.zip"
    out.write_bytes(b"x")
    assert run(src, out).exit_code == 1
    result = run(src, out, "--force", "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["hop"]["summary"]["versions_dropped"] == 2
