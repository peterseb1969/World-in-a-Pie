"""`diff`: rows added, removed and changed between two archives, by key."""

from test_transform import DOC_M, DOC_S, FILE_S, NS, _build, _drop_spells, _monster, _rows, _run
from wip_archive.archive import ArchiveReader
from wip_archive.diff import diff_archives


def _diff(a, b):
    with ArchiveReader(a) as ra, ArchiveReader(b) as rb:
        return diff_archives(ra, rb)


def test_an_archive_is_identical_to_itself(tmp_path):
    a = _build(tmp_path / "a.zip")
    result = _diff(a, a)
    assert result.identical
    assert result.types[(NS, "documents")].unchanged == 3


def test_a_cut_shows_as_removed_rows_and_added_referents(tmp_path):
    a = _build(tmp_path / "a.zip")
    b = tmp_path / "b.zip"
    _run(a, b, _drop_spells)
    result = _diff(a, b)
    assert not result.identical
    docs = result.types[(NS, "documents")]
    assert (docs.removed, docs.added, docs.changed) == (1, 0, 0)
    assert docs.removed_samples == [f"{DOC_S}/1"]
    assert result.types[(NS, "files")].removed == 1
    assert result.types[(NS, "registry_entries")].removed == 2  # the spell and its file
    # The spell left the registry rows and arrived in the referent table; the
    # input's unused wip referent left it.
    assert result.referents.added_samples == [DOC_S]
    assert result.referents.removed == 1
    assert result.blobs.removed_samples == [FILE_S]
    assert result.chain_a == [] and len(result.chain_b) == 1


def test_a_changed_row_names_the_fields_that_differ(tmp_path):
    a = _build(tmp_path / "a.zip")
    rows = _rows()
    edited = _monster(2)
    edited["data"] = {**edited["data"], "name": "Smaug the Golden"}
    edited["status"] = "inactive"
    rows["documents"] = [rows["documents"][0], rows["documents"][1], edited]
    b = _build(tmp_path / "b.zip", rows)
    docs = _diff(a, b).types[(NS, "documents")]
    assert (docs.changed, docs.unchanged) == (1, 2)
    assert docs.changed_samples == {f"{DOC_M}/2": ["data", "status"]}


def test_key_order_is_not_a_change(tmp_path):
    a = _build(tmp_path / "a.zip")
    rows = _rows()
    rows["documents"] = [dict(reversed(list(d.items()))) for d in rows["documents"]]
    b = _build(tmp_path / "b.zip", rows)
    assert _diff(a, b).identical


def test_to_dict_is_json_ready(tmp_path):
    import json

    a = _build(tmp_path / "a.zip")
    payload = _diff(a, a).to_dict()
    json.dumps(payload)
    assert payload["identical"] is True
