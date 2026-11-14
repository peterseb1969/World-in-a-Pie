"""export-csv's latest-only filter counts a row's version as the model does.

The model counts a missing, null or zero version as 1 and a numeric string
as its integer. The filter compared the row's raw version with the model's
coerced one, so such a row was left out of the export, and a single-row
document vanished from the CSV with nothing said."""

from test_export_csv import _export, _read
from test_filter import M2, NS, S1, _rows, _write


def test_a_row_with_a_string_or_missing_version_is_exported(tmp_path):
    rows = _rows()
    for doc in rows["documents"]:
        if doc["document_id"] == M2:
            doc["version"] = "1"
        elif doc["document_id"] == S1:
            del doc["version"]
    path = _write(tmp_path / "in.zip", {NS: rows})

    files = _export(path, tmp_path / "out")

    by_template = {f.template: f.rows for f in files}
    assert by_template["MONSTER"] == 2 and by_template["SPELL"] == 2
    _header, monsters = _read(tmp_path / "out" / NS / "MONSTER.csv")
    assert M2 in {r["document_id"] for r in monsters}
    _header, spells = _read(tmp_path / "out" / NS / "SPELL.csv")
    assert S1 in {r["document_id"] for r in spells}
