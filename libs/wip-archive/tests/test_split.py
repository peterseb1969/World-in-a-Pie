"""`split`: parts by namespace or island, covering every row, each written
through the transform core."""

import pytest
from test_filter import (
    LOV_SIZE,
    NAME,
    NS,
    OTHER,
    S1,
    SPELL,
    _doc,
    _id,
    _reference,
    _registry,
    _rows,
    _write,
)
from wip_archive.archive import ArchiveReader
from wip_archive.model import ArchiveModel
from wip_archive.split import SplitError, plan_split
from wip_archive.transform import run_transform

LONE, LONE_DOC = _id("c", 31), _id("d", 31)
OTHER_TPL, OTHER_DOC = _id("c", 32), _id("d", 32)
FOREIGN_TPL, FOREIGN_DOC = _id("c", 33), _id("d", 33)


def _lone_rows():
    rows = _rows()
    rows["templates"].append({"template_id": LONE, "version": 1, "value": "LONE",
                              "namespace": NS, "status": "active", "identity_fields": [],
                              "fields": [NAME]})
    rows["documents"].append({**_doc(LONE_DOC, SPELL, {"name": "x"}), "template_id": LONE,
                              "identity_hash": ""})
    # A document pinned to a template this archive does not carry.
    rows["documents"].append({**_doc(FOREIGN_DOC, SPELL, {"name": "y"}),
                              "template_id": FOREIGN_TPL, "identity_hash": ""})
    rows["registry_entries"] += [_registry(LONE, "templates"), _registry(LONE_DOC, "documents"),
                                 _registry(FOREIGN_DOC, "documents")]
    return rows


def _parts(path, by):
    with ArchiveReader(path) as reader:
        return plan_split(reader, ArchiveModel.load(reader), by)


def _apply(path, part, out):
    return run_transform(path, out, transform="split", toolkit_version="t",
                         params={"by": "x", "part": part.name}, keep_row=part.keep_row)


def _doc_ids(path):
    with ArchiveReader(path) as reader:
        return [d["document_id"] for ns in reader.list_namespaces()
                for d in reader.read_entities("documents", namespace=ns)]


class TestByIsland:
    def test_parts_cover_every_document_exactly_once(self, tmp_path):
        src = _write(tmp_path / "in.zip", {NS: _lone_rows()})
        parts = _parts(src, "island")
        assert [p.name for p in parts] == ["island-1", "island-2", "unattached"]
        assert "MONSTER" in parts[0].description and "LONE" in parts[1].description
        seen = []
        for part in parts:
            out = tmp_path / f"{part.name}.zip"
            result = _apply(src, part, out)
            assert result.verdict != "fail", (part.name, [f.to_dict() for f in result.model.findings()])
            seen += _doc_ids(out)
        assert sorted(seen) == sorted(_doc_ids(src))

    def test_relationships_stay_with_their_endpoints_and_nothing_is_cut(self, tmp_path):
        src = _write(tmp_path / "in.zip", {NS: _lone_rows()})
        result = _apply(src, _parts(src, "island")[0], tmp_path / "one.zip")
        assert result.referents == 0
        assert result.verdict == "pass"

    def test_unattached_holds_unused_vocabularies_and_foreign_documents(self, tmp_path):
        src = _write(tmp_path / "in.zip", {NS: _lone_rows()})
        unattached = _parts(src, "island")[-1]
        out = tmp_path / "u.zip"
        _apply(src, unattached, out)
        assert _doc_ids(out) == [FOREIGN_DOC]
        with ArchiveReader(out) as reader:
            lovs = [t["terminology_id"] for t in reader.read_entities("terminologies", namespace=NS)]
        assert lovs == [LOV_SIZE]


class TestByNamespace:
    def test_a_reference_into_another_part_becomes_a_referent(self, tmp_path):
        other = {
            "templates": [{"template_id": OTHER_TPL, "version": 1, "value": "NOTE",
                           "namespace": OTHER, "status": "active", "identity_fields": [],
                           "fields": [NAME]}],
            "documents": [{**_doc(OTHER_DOC, SPELL, {"name": "n"}), "namespace": OTHER,
                           "template_id": OTHER_TPL, "identity_hash": "",
                           "references": [_reference("about", S1, SPELL)]}],
            "registry_entries": [_registry(OTHER_TPL, "templates", OTHER),
                                 _registry(OTHER_DOC, "documents", OTHER)],
        }
        src = _write(tmp_path / "two.zip", {NS: _rows(), OTHER: other})
        parts = _parts(src, "namespace")
        assert [p.name for p in parts] == [NS, OTHER]
        out = tmp_path / "other.zip"
        result = _apply(src, parts[1], out)
        with ArchiveReader(out) as reader:
            assert reader.list_namespaces() == [OTHER]
            # The spell, and its template, which the reference snapshot names.
            assert {r["entry_id"] for r in reader.read_referents()} == {S1, SPELL}
        assert result.verdict != "fail"


def test_an_unknown_by_is_refused(tmp_path):
    src = _write(tmp_path / "in.zip", {NS: _rows()})
    with pytest.raises(SplitError, match="unknown --by"):
        _parts(src, "colour")


def _file_ids(path):
    with ArchiveReader(path) as reader:
        return [f["file_id"] for ns in reader.list_namespaces()
                for f in reader.read_entities("files", namespace=ns)]


def test_a_file_no_document_references_goes_to_unattached(tmp_path):
    """Files followed only their documents, so an unreferenced file (and its
    Registry row) was in no part. Provenance: CASE-1111."""
    loose = _id("f", 41)
    rows = _rows()
    rows["files"].append({"file_id": loose, "namespace": NS, "filename": "loose.bin",
                          "status": "active"})
    rows["registry_entries"].append(_registry(loose, "files"))
    src = _write(tmp_path / "in.zip", {NS: rows})
    parts = _parts(src, "island")
    assert parts[-1].name == "unattached" and "1 files no document references" in parts[-1].description
    seen = []
    for part in parts:
        out = tmp_path / f"{part.name}.zip"
        assert _apply(src, part, out).verdict != "fail"
        seen += _file_ids(out)
    assert sorted(seen) == sorted(_file_ids(src))
