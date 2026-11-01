"""`strip-blobs`: file rows and blobs leave; referenced files become
referents, unreferenced ones leave no trace."""

from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.strip_blobs import StripBlobsPlan
from wip_archive.transform import run_transform

NS = "demo"
TPL = "0190c000-0000-7000-0000-0000000000t1"
DOC = "019d0000-0000-7000-8000-0000000000d1"
USED, LOOSE = "019f0000-0000-7000-8000-0000000000f1", "019f0000-0000-7000-8000-0000000000f2"


def _registry(entry_id, entity_type, key):
    return {"entry_id": entry_id, "namespace": NS, "entity_type": entity_type,
            "primary_composite_key": key, "primary_composite_key_hash": "h", "synonyms": [],
            "status": "active"}


def _archive(tmp_path):
    path = tmp_path / "in.zip"
    writer = ArchiveWriter(path, default_namespace=NS)
    writer.add_entity("templates", {
        "template_id": TPL, "version": 1, "value": "ITEM", "namespace": NS, "status": "active",
        "identity_fields": [], "fields": [{"name": "scan", "type": "file"}]}, namespace=NS)
    writer.add_entity("documents", {
        "document_id": DOC, "namespace": NS, "template_id": TPL, "template_version": 1,
        "version": 1, "status": "active", "identity_hash": "", "data": {"scan": USED},
        "term_references": [], "references": [],
        "file_references": [{"field_path": "scan", "file_id": USED}]}, namespace=NS)
    for file_id in (USED, LOOSE):
        writer.add_entity("files", {"file_id": file_id, "namespace": NS, "status": "active",
                                    "checksum": f"sum-{file_id}"}, namespace=NS)
        writer.add_blob(file_id, b"bytes")
    for row in (_registry(TPL, "templates", {"ns": NS, "value": "ITEM"}),
                _registry(DOC, "documents", {}),
                _registry(USED, "files", {"checksum": f"sum-{USED}"}),
                _registry(LOOSE, "files", {"checksum": f"sum-{LOOSE}"})):
        writer.add_entity("registry_entries", row, namespace=NS)
    writer.write({"format_version": "3.1", "namespace": NS, "include_files": True, "counts": {},
                  "namespaces": [{"prefix": NS, "counts": {}}],
                  "referents": writer.write_referents_file([])})
    return path


def test_files_leave_and_a_referenced_one_becomes_a_referent(tmp_path):
    out = tmp_path / "out.zip"
    result = run_transform(_archive(tmp_path), out, transform="strip-blobs",
                           toolkit_version="t", params={}, keep_row=StripBlobsPlan().keep_row)
    assert result.verdict == "pass", [f.to_dict() for f in result.model.findings()]
    with ArchiveReader(out) as reader:
        assert list(reader.read_entities("files", namespace=NS)) == []
        assert reader.list_blobs() == []
        [referent] = list(reader.read_referents())
        manifest = reader.read_manifest_raw()
        [doc] = list(reader.read_entities("documents", namespace=NS))
    # The referenced file travels as its Registry row: its checksum key is
    # what a restore resolves it by.
    assert referent["entry_id"] == USED
    assert referent["primary_composite_key"] == {"checksum": f"sum-{USED}"}
    assert doc["file_references"] == [{"field_path": "scan", "file_id": USED}]
    assert manifest["include_files"] is False
    assert result.blobs_dropped == 2
    assert result.dropped["files"] == 2
