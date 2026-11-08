"""`export-csv`: one CSV per template, latest versions by default, data
flattened by dot path, terms as value plus term id."""

import csv
import json

import pytest
from test_filter import M1, M2, MONSTER, NS, RED, _doc, _registry, _rows, _write
from wip_archive.archive import ArchiveReader
from wip_archive.export_csv import ExportExistsError, export_csv
from wip_archive.model import ArchiveModel

ODD = "019d0000-0000-7000-8000-00000000odd1"


def _export(path, out, **kwargs):
    with ArchiveReader(path) as reader:
        return export_csv(reader, ArchiveModel.load(reader), out, **kwargs)


def _read(path):
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return reader.fieldnames, list(reader)


@pytest.fixture
def archive(tmp_path):
    return _write(tmp_path / "in.zip", {NS: _rows()})


def test_one_file_per_template_latest_versions(archive, tmp_path):
    files = _export(archive, tmp_path / "out")
    assert [(f.namespace, f.template, f.rows) for f in files] == [
        (NS, "KNOWS", 1), (NS, "LAIR", 1), (NS, "MONSTER", 2), (NS, "SPELL", 2)]
    header, rows = _read(tmp_path / "out" / NS / "MONSTER.csv")
    # Template field order, the term id right after its value.
    assert header == ["document_id", "version", "status", "name", "colour", "colour__term_id",
                      "favourite"]
    smaug = next(r for r in rows if r["document_id"] == M1)
    assert (smaug["version"], smaug["colour"], smaug["colour__term_id"]) == ("2", "red", RED)
    glaurung = next(r for r in rows if r["document_id"] == M2)
    assert glaurung["colour"] == "" and glaurung["favourite"] == ""


def test_all_versions_writes_every_row(archive, tmp_path):
    files = _export(archive, tmp_path / "out", all_versions=True)
    assert next(f for f in files if f.template == "MONSTER").rows == 3


def test_nested_objects_arrays_and_term_arrays(tmp_path):
    rows = _rows()
    odd = _doc(ODD, MONSTER, {"name": "Odd", "lair": {"city": "Erebor", "deep": True},
                              "tags": ["red", "blue"], "count": None},
               terms=[{"field_path": "tags[1]", "term_id": "t-blue"},
                      {"field_path": "tags[0]", "term_id": "t-red"}])
    rows["documents"].append(odd)
    rows["registry_entries"].append(_registry(ODD, "documents"))
    path = _write(tmp_path / "in.zip", {NS: rows})
    _export(path, tmp_path / "out")
    header, out = _read(tmp_path / "out" / NS / "MONSTER.csv")
    row = next(r for r in out if r["document_id"] == ODD)
    assert row["lair.city"] == "Erebor" and row["lair.deep"] == "true"
    assert json.loads(row["tags"]) == ["red", "blue"]
    assert json.loads(row["tags__term_id"]) == ["t-red", "t-blue"]  # item order
    assert row["count"] == ""
    # Undeclared paths follow the declared ones, sorted.
    assert header.index("favourite") < header.index("lair.city") < header.index("tags")


def test_an_existing_csv_is_refused_and_a_dry_run_writes_nothing(archive, tmp_path):
    out = tmp_path / "out"
    planned = _export(archive, out, dry_run=True)
    assert planned and not out.exists()
    _export(archive, out)
    with pytest.raises(ExportExistsError):
        _export(archive, out)
    _export(archive, out, overwrite=True)


def test_a_data_field_named_like_an_export_column_keeps_its_value(tmp_path):
    """A data field status, version or document_id, or one ending in
    __term_id, was overwritten by the export's own column of that name and
    the header held the name twice. Provenance: CASE-1111."""
    rows = _rows()
    odd = _doc(ODD, MONSTER, {"name": "Odd", "status": "sleeping", "version": "7b",
                              "colour": "red", "colour__term_id": "mine"},
               terms=[{"field_path": "colour", "term_id": RED}])
    rows["documents"].append(odd)
    rows["registry_entries"].append(_registry(ODD, "documents"))
    path = _write(tmp_path / "in.zip", {NS: rows})
    _export(path, tmp_path / "out")
    header, out = _read(tmp_path / "out" / NS / "MONSTER.csv")
    assert len(header) == len(set(header))
    row = next(r for r in out if r["document_id"] == ODD)
    assert row["status"] == "active" and row["data:status"] == "sleeping"
    assert row["data:version"] == "7b"
    assert row["colour__term_id"] == RED and row["data:colour__term_id"] == "mine"


def test_a_column_taken_twice_refuses_the_export(tmp_path):
    from wip_archive.export_csv import ExportCollisionError

    rows = _rows()
    odd = _doc(ODD, MONSTER, {"name": "Odd", "status": "a", "data:status": "b"})
    rows["documents"].append(odd)
    rows["registry_entries"].append(_registry(ODD, "documents"))
    path = _write(tmp_path / "in.zip", {NS: rows})
    with pytest.raises(ExportCollisionError, match="data:status"):
        _export(path, tmp_path / "out")
    assert not (tmp_path / "out").exists()
