"""`wip-toolkit export-csv`: one CSV per template, refusal before writing."""

import csv
import json

from click.testing import CliRunner
from test_filter_cli import M1, NS, archive  # noqa: F401 — pytest fixture
from wip_toolkit.cli import main


def run(*args):
    return CliRunner().invoke(main, ["export-csv", *map(str, args)])


def test_writes_one_csv_per_template(archive, tmp_path):  # noqa: F811
    out = tmp_path / "csv"
    result = run(archive, out, "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert sorted(f["template"] for f in payload["files"]) == ["MONSTER", "SPELL"]
    with (out / NS / "MONSTER.csv").open(newline="") as fh:
        [row] = list(csv.DictReader(fh))
    assert row["document_id"] == M1 and row["name"] == "Smaug"


def test_an_existing_csv_needs_force_and_dry_run_writes_nothing(archive, tmp_path):  # noqa: F811
    out = tmp_path / "csv"
    assert run(archive, out, "--dry-run").exit_code == 0 and not out.exists()
    assert run(archive, out).exit_code == 0
    refused = run(archive, out)
    assert refused.exit_code == 1 and "Nothing written" in refused.output
    assert run(archive, out, "--force").exit_code == 0
