"""Batch-13 review lows on archive reading (d6cc8484, 917019ca).

- Bytes corrupt below the JSON layer (not UTF-8, a bad CRC, a broken deflate
  stream) escaped the reader untyped; they are ManifestParseError /
  EntityParseError now, so a caller refuses them like any malformed archive.
- discard_blob raised on an id that is not a safe segment, from inside the
  caller's per-file failure handler; nothing can have been written for such
  an id, so it is a no-op.
- check_member_name applied only '.', '..' and empty to member names outside
  namespaces/ and blobs/; every segment now passes is_safe_segment (no
  drive letter, no control character).
- Manifest.is_v3 needs a stated format_version of the form 3 or 3.N.
"""

import json
import zipfile

import pytest
from wip_archive.archive import ArchiveReader, ArchiveWriter
from wip_archive.exceptions import EntityParseError, ManifestParseError, UnsafePathError
from wip_archive.models import Manifest
from wip_archive.paths import check_member_name

GOOD = {"format_version": "3.1", "namespaces": [{"prefix": "ns1"}]}


def _zip(path, members, compression=zipfile.ZIP_STORED):
    with zipfile.ZipFile(path, "w", compression=compression) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_a_manifest_that_is_not_utf8_is_a_parse_error(tmp_path):
    path = _zip(tmp_path / "a.zip", {"manifest.json": b'{"format_version": "3.1\xff"}'})
    with ArchiveReader(path) as reader, pytest.raises(ManifestParseError):
        reader.read_manifest_raw()


def test_a_corrupt_manifest_member_is_a_parse_error(tmp_path):
    path = _zip(tmp_path / "a.zip", {"manifest.json": json.dumps(GOOD) * 50},
                compression=zipfile.ZIP_DEFLATED)
    raw = bytearray(path.read_bytes())
    # Flip bytes inside the compressed data (after the 30-byte local header
    # and the member name), so the CRC check or the inflater fails.
    start = 30 + len("manifest.json")
    for i in range(start + 5, start + 25):
        raw[i] ^= 0xFF
    path.write_bytes(bytes(raw))
    with ArchiveReader(path) as reader, pytest.raises(ManifestParseError):
        reader.read_manifest_raw()


def test_an_entity_line_that_is_not_utf8_is_a_parse_error(tmp_path):
    path = _zip(tmp_path / "a.zip", {
        "manifest.json": json.dumps(GOOD),
        "namespaces/ns1/terms.jsonl": b'{"term_id": "t\xff"}\n',
    })
    with ArchiveReader(path) as reader:
        with pytest.raises(EntityParseError, match="terms.jsonl"):
            list(reader.read_entities("terms", namespace="ns1"))
        with pytest.raises(EntityParseError, match="terms.jsonl"):
            reader.entity_count("terms", namespace="ns1")


@pytest.mark.parametrize("file_id", ["a/b", "../x", "C:evil", ""])
def test_discard_blob_of_an_unsafe_id_is_a_no_op(tmp_path, file_id):
    writer = ArchiveWriter(tmp_path / "out.zip", tmp_dir=str(tmp_path))
    writer.discard_blob(file_id)
    with pytest.raises(UnsafePathError), writer.open_blob(file_id):
        pass


@pytest.mark.parametrize("name", ["C:/Users/ops/config.json", "Z:payload", "x/terms\x0dk.jsonl"])
def test_member_names_follow_the_segment_rule(name):
    with pytest.raises(UnsafePathError):
        check_member_name(name)


def test_ordinary_member_names_pass():
    for name in ("manifest.json", "namespaces/ns1/terms.jsonl", "blobs/F1", "referents.jsonl"):
        check_member_name(name)


@pytest.mark.parametrize(("version", "v3"), [
    ("3", True), ("3.0", True), ("3.1", True), ("3.12", True),
    ("30.0", False), ("3-", False), ("3.1.2", False), ("2.0", False), ("4.0", False),
])
def test_is_v3(version, v3):
    assert Manifest(format_version=version).is_v3() is v3


def test_a_manifest_without_format_version_is_not_v3():
    assert not Manifest(**{"namespaces": []}).is_v3()
    assert Manifest().format_label().startswith("unstated")
