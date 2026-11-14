"""Two archive-reading fixes, through the toolkit.

A row whose version is not a number makes every archive command stop with
a message naming the row and exit 1, not a traceback. A v2 archive whose
manifest records no export time converts to a v3 archive that records none
either: the converter used to write the moment it read the manifest, and a
later restore reported that as the export time. inspect shows a missing
export time as unknown."""

import json
import zipfile

from click.testing import CliRunner
from rich.console import Console
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.model import ArchiveModel
from wip_toolkit.cli import main
from wip_toolkit.convert_archive import convert_archive
from wip_toolkit.inspect_view import _render_header

NS = "aa"
TPL = "0190c000-0000-7000-0000-0000000000t1"
DOC_A = "019d0000-0000-7000-8000-0000000000a1"
DOC_B = "019d0000-0000-7000-8000-0000000000b1"


def _doc(doc_id, version, refs=()):
    return {"document_id": doc_id, "namespace": NS, "template_id": TPL, "template_version": 1,
            "version": version, "status": "active", "identity_hash": "", "data": {},
            "term_references": [], "references": list(refs), "file_references": []}


def _archive(tmp_path, documents):
    path = tmp_path / "in.zip"
    writer = ArchiveWriter(path, default_namespace=NS)
    writer.add_entity("templates", {"template_id": TPL, "version": 1, "value": "ITEM",
                                    "namespace": NS, "status": "active", "identity_fields": [],
                                    "fields": []}, namespace=NS)
    for doc in documents:
        writer.add_entity("documents", doc, namespace=NS)
    writer.write({"format_version": "3.1", "namespace": NS, "include_all_versions": True,
                  "counts": {}, "namespaces": [{"prefix": NS, "counts": {}}],
                  "referents": writer.write_referents_file([])})
    return path


def test_latest_only_names_a_pin_whose_version_is_not_a_number(tmp_path):
    pin = {"field_path": "other", "reference_type": "document", "version_strategy": "pinned",
           "resolved": {"document_id": DOC_A, "version": "latest"}}
    src = _archive(tmp_path, [_doc(DOC_A, 1), _doc(DOC_B, 1, refs=[pin])])
    result = CliRunner().invoke(main, ["latest-only", str(src), str(tmp_path / "out.zip")])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Unreadable archive" in result.output
    assert DOC_A in result.output and "'latest'" in result.output


def test_export_csv_names_a_document_whose_version_is_not_a_number(tmp_path):
    src = _archive(tmp_path, [_doc(DOC_A, "v1")])
    result = CliRunner().invoke(main, ["export-csv", str(src), str(tmp_path / "out")])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Unreadable archive" in result.output and DOC_A in result.output


def test_a_v2_archive_without_an_export_time_converts_without_one(tmp_path):
    old, new = tmp_path / "old.zip", tmp_path / "new.zip"
    with zipfile.ZipFile(old, "w") as zf:
        zf.writestr("manifest.json", json.dumps(
            {"format_version": "2.0", "namespace": "legacy", "counts": {}}))
        zf.writestr("terms.jsonl", '{"term_id":"T1"}\n')

    convert_archive(old, new)

    with ArchiveReader(new) as reader:
        assert reader.read_manifest_raw()["exported_at"] is None
        assert reader.read_manifest().exported_at is None


def test_a_v2_archive_keeps_the_export_time_it_recorded(tmp_path):
    old, new = tmp_path / "old.zip", tmp_path / "new.zip"
    with zipfile.ZipFile(old, "w") as zf:
        zf.writestr("manifest.json", json.dumps(
            {"format_version": "2.0", "namespace": "legacy", "counts": {},
             "exported_at": "2025-03-04T05:06:07Z"}))

    convert_archive(old, new)

    with ArchiveReader(new) as reader:
        assert reader.read_manifest().exported_at.isoformat() == "2025-03-04T05:06:07+00:00"


def test_inspect_shows_a_missing_export_time_as_unknown(tmp_path):
    src = _archive(tmp_path, [])
    console = Console(record=True, width=200)
    with ArchiveReader(src) as reader:
        _render_header(ArchiveModel.load(reader), console)
    exported = next(line for line in console.export_text().splitlines() if "Exported at" in line)
    assert "unknown" in exported
