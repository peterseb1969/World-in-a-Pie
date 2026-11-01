"""`wip-toolkit diff`: exit 0 identical, 1 different, 2 unreadable."""

import json

from click.testing import CliRunner
from test_filter_cli import S1, archive  # noqa: F401 — pytest fixture
from wip_toolkit.cli import main


def run(*args):
    return CliRunner().invoke(main, ["diff", *map(str, args)])


def test_identical_archives_exit_0(archive):  # noqa: F811
    result = run(archive, archive)
    assert result.exit_code == 0, result.output
    assert "identical" in result.output


def test_a_filtered_archive_differs(archive, tmp_path):  # noqa: F811
    out = tmp_path / "out.zip"
    assert CliRunner().invoke(main, ["filter", str(archive), str(out), "MONSTER"]).exit_code == 0
    result = run(archive, out, "--json")
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["schema"] == "1.0" and payload["identical"] is False
    docs = next(t for t in payload["types"] if t["entity_type"] == "documents")
    assert docs["removed"] == 1 and docs["removed_samples"] == [f"{S1}/1"]
    assert payload["referents"]["added_samples"] == [S1]
    assert [h["transform"] for h in payload["chain_b"]] == ["filter"]


def test_an_unreadable_archive_exits_2(archive, tmp_path):  # noqa: F811
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    assert run(archive, bad).exit_code == 2
