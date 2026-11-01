"""`wip-toolkit strip-blobs`: files and blobs leave, referenced files become
referents."""

import json

from click.testing import CliRunner
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_toolkit.cli import main

NS = "aa"
TPL = "0190c000-0000-7000-0000-0000000000t1"
DOC = "019d0000-0000-7000-8000-0000000000d1"
FILE = "019f0000-0000-7000-8000-0000000000f1"


def _archive(tmp_path):
    path = tmp_path / "in.zip"
    writer = ArchiveWriter(path, default_namespace=NS)
    writer.add_entity("templates", {"template_id": TPL, "version": 1, "value": "ITEM",
                                    "namespace": NS, "status": "active", "identity_fields": [],
                                    "fields": [{"name": "scan", "type": "file"}]}, namespace=NS)
    writer.add_entity("documents", {
        "document_id": DOC, "namespace": NS, "template_id": TPL, "template_version": 1,
        "version": 1, "status": "active", "identity_hash": "", "data": {"scan": FILE},
        "term_references": [], "references": [],
        "file_references": [{"field_path": "scan", "file_id": FILE}]}, namespace=NS)
    writer.add_entity("files", {"file_id": FILE, "namespace": NS, "status": "active"},
                      namespace=NS)
    writer.add_blob(FILE, b"content")
    for entry_id, entity_type in ((TPL, "templates"), (DOC, "documents"), (FILE, "files")):
        writer.add_entity("registry_entries", {
            "entry_id": entry_id, "namespace": NS, "entity_type": entity_type,
            "primary_composite_key": {"checksum": "x"} if entity_type == "files" else {},
            "primary_composite_key_hash": "h", "synonyms": [], "status": "active"},
            namespace=NS)
    writer.write({"format_version": "3.1", "namespace": NS, "include_files": True, "counts": {},
                  "namespaces": [{"prefix": NS, "counts": {}}],
                  "referents": writer.write_referents_file([])})
    return path


def test_files_leave_and_the_referenced_one_is_a_referent(tmp_path):
    out = tmp_path / "out.zip"
    result = CliRunner().invoke(main, ["strip-blobs", str(_archive(tmp_path)), str(out),
                                       "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["verdict"] == "pass"
    assert payload["hop"]["transform"] == "strip-blobs"
    assert payload["hop"]["summary"]["blobs_dropped"] == 1
    with ArchiveReader(out) as reader:
        assert reader.list_blobs() == []
        assert [r["entry_id"] for r in reader.read_referents()] == [FILE]


def test_an_existing_output_needs_force(tmp_path):
    src = _archive(tmp_path)
    out = tmp_path / "out.zip"
    out.write_bytes(b"x")
    assert CliRunner().invoke(main, ["strip-blobs", str(src), str(out)]).exit_code == 1
    assert CliRunner().invoke(main, ["strip-blobs", str(src), str(out), "--force"]).exit_code == 0
