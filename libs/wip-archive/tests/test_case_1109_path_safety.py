"""Archive-supplied names never escape the paths they are joined onto, and a
malformed manifest or entity line is a typed ArchiveError (CASE-1109)."""

import json
import zipfile

import pytest
from test_filter import NS, _rows, _write
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.exceptions import (
    ArchiveError,
    EntityParseError,
    ManifestValidationError,
    UnsafePathError,
)
from wip_archive.export_csv import export_csv
from wip_archive.model import ArchiveModel
from wip_archive.paths import is_safe_segment
from wip_archive.transform import run_transform

GOOD_MANIFEST = {"format_version": "3.1", "namespaces": [{"prefix": "ns1"}]}


def _scratch(tmp_path):
    scratch = tmp_path / "a" / "b" / "scratch"
    scratch.mkdir(parents=True)
    return scratch


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


@pytest.mark.parametrize("value", ["wip", "my-ns.v2", "019d0000-0000-7000-8000-000000000001",
                                   "MONSTER_v2", "a b"])
def test_safe_segments_pass(value):
    assert is_safe_segment(value)


@pytest.mark.parametrize("value", ["", ".", "..", "a/b", "/abs", "../up", "a\\b", "x\x00y",
                                   "tab\there", "C:evil", None, 7])
def test_unsafe_segments_refused(value):
    assert not is_safe_segment(value)


def test_writer_refuses_an_escaping_namespace(tmp_path):
    writer = ArchiveWriter(tmp_path / "out.zip", tmp_dir=_scratch(tmp_path))
    target = tmp_path / "escaped"
    with pytest.raises(UnsafePathError):
        writer.add_entity("terms", {"term_id": "t"}, namespace=f"../../{target.name}")
    with pytest.raises(UnsafePathError):
        writer.add_entity("terms", {"term_id": "t"}, namespace=str(target))
    assert not target.exists()


@pytest.mark.parametrize("file_id", ["../../overwritten", "/tmp/overwritten-by-wip-archive",
                                     "sub/dir"])
def test_writer_refuses_an_escaping_file_id(tmp_path, file_id):
    writer = ArchiveWriter(tmp_path / "out.zip", tmp_dir=_scratch(tmp_path))
    with pytest.raises(UnsafePathError):
        writer.add_blob(file_id, b"x")
    with pytest.raises(UnsafePathError), writer.open_blob(file_id) as fh:
        fh.write(b"x")
    with pytest.raises(UnsafePathError):
        writer.discard_blob(file_id)
    assert not (tmp_path / "overwritten").exists()


@pytest.mark.parametrize("member", [
    "namespaces/../../evil/terms.jsonl",
    "namespaces/../terms.jsonl",
    "blobs/../../evil",
    "blobs/a/b",
    "/etc/evil",
    "namespaces\\ns1\\terms.jsonl",
])
def test_reader_refuses_unsafe_member_names_at_open(tmp_path, member):
    path = _zip(tmp_path / "bad.zip", {"manifest.json": json.dumps(GOOD_MANIFEST), member: "{}\n"})
    with pytest.raises(UnsafePathError):
        ArchiveReader(path)


def test_reader_accepts_an_ordinary_archive(tmp_path):
    path = _zip(tmp_path / "ok.zip", {
        "manifest.json": json.dumps(GOOD_MANIFEST),
        "namespaces/ns1/terms.jsonl": '{"term_id": "t"}\n',
        "blobs/019d0000-0000-7000-8000-000000000001": "bytes",
    })
    with ArchiveReader(path) as reader:
        assert reader.list_namespaces() == ["ns1"]
        assert list(reader.read_entities("terms", namespace="ns1")) == [{"term_id": "t"}]


def test_transform_of_a_crafted_namespace_writes_nothing_outside(tmp_path):
    """A transform takes its namespaces from the archive and stages each one
    as a directory. A prefix that climbs out of the staging tree (namespaces/
    -> wip-export-* -> wip-transform-* -> scratch -> b -> a -> tmp_path) is
    refused, not followed."""
    prefix = "../../../../../../escaped"
    manifest = {"format_version": "3.1", "namespaces": [{"prefix": prefix}]}
    src = _zip(tmp_path / "in.zip", {"manifest.json": json.dumps(manifest),
                                    f"namespaces/{prefix}/terms.jsonl": '{"term_id": "t"}\n'})
    with pytest.raises(UnsafePathError):
        run_transform(src, tmp_path / "out.zip", transform="filter", toolkit_version="9.9.9",
                      params={}, keep_row=lambda *_: True, tmp_dir=_scratch(tmp_path))
    assert not (tmp_path / "escaped").exists()


def test_writer_refuses_a_manifest_prefix_even_without_a_member(tmp_path):
    """Defence behind the reader: the writer checks the prefix itself, so a
    caller that hands it an archive-derived name directly is covered too."""
    writer = ArchiveWriter(tmp_path / "out.zip", tmp_dir=_scratch(tmp_path))
    with pytest.raises(UnsafePathError):
        writer.add_entity("terms", {"term_id": "t"}, namespace="../../../escaped")
    assert not (tmp_path / "escaped").exists()


def test_export_csv_refuses_an_escaping_template_value(tmp_path):
    rows = _rows()
    for row in rows["templates"]:
        if row.get("value") == "MONSTER":
            row["value"] = "../../escaped"
    path = _write(tmp_path / "in.zip", {NS: rows})
    with ArchiveReader(path) as reader, pytest.raises(UnsafePathError):
        export_csv(reader, ArchiveModel.load(reader), tmp_path / "out" / "deeper")
    assert not (tmp_path / "escaped.csv").exists()


def test_an_invalid_manifest_is_a_typed_archive_error(tmp_path):
    path = _zip(tmp_path / "bad.zip", {
        "manifest.json": json.dumps({"format_version": "3.1", "namespaces": "not-a-list"}),
    })
    with ArchiveReader(path) as reader, pytest.raises(ManifestValidationError) as exc:
        reader.read_manifest()
    assert isinstance(exc.value, ArchiveError)


def test_a_corrupt_entity_line_is_a_typed_archive_error(tmp_path):
    path = _zip(tmp_path / "bad.zip", {
        "manifest.json": json.dumps(GOOD_MANIFEST),
        "namespaces/ns1/terms.jsonl": '{"term_id": "t"}\n{not json\n',
    })
    with ArchiveReader(path) as reader, pytest.raises(EntityParseError) as exc:
        list(reader.read_entities("terms", namespace="ns1"))
    assert "namespaces/ns1/terms.jsonl" in str(exc.value)
    assert isinstance(exc.value, ArchiveError)
