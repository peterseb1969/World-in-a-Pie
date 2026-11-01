"""`wip-toolkit split`: parts into a directory, refusal before writing any."""

import json

from click.testing import CliRunner
from test_filter_cli import archive  # noqa: F401 — pytest fixture
from wip_archive.archive import ArchiveReader
from wip_toolkit.cli import main


def run(*args):
    return CliRunner().invoke(main, ["split", *map(str, args)])


def test_by_namespace_writes_one_part(archive, tmp_path):  # noqa: F811
    out = tmp_path / "parts"
    result = run(archive, out, "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [p["part"] for p in payload["parts"]] == ["aa"]
    with ArchiveReader(out / "aa.zip") as reader:
        hop = reader.read_manifest_raw()["derived_from"][-1]
    assert hop["transform"] == "split" and hop["params"] == {"by": "namespace", "part": "aa"}


def test_by_island_and_dry_run(archive, tmp_path):  # noqa: F811
    out = tmp_path / "parts"
    result = run(archive, out, "--by", "island", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "island-1.zip" in result.output and "dry run" in result.output
    assert not out.exists()


def test_an_existing_part_refuses_before_writing_any(archive, tmp_path):  # noqa: F811
    out = tmp_path / "parts"
    out.mkdir()
    (out / "aa.zip").write_bytes(b"keep")
    result = run(archive, out)
    assert result.exit_code == 1 and "Nothing written" in result.output
    assert (out / "aa.zip").read_bytes() == b"keep"
    assert run(archive, out, "--force").exit_code == 0
