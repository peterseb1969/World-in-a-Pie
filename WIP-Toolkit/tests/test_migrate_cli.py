"""`wip-toolkit migrate`: mapping file handling and refusals. The live
comparison is tests/integration/test_migrate_parity.py."""

import json

from click.testing import CliRunner
from test_filter_cli import archive  # noqa: F401 — pytest fixture
from wip_toolkit.cli import main


def run(*args):
    return CliRunner().invoke(main, ["migrate", *map(str, args)])


def test_an_unreadable_mapping_is_refused(archive, tmp_path):  # noqa: F811
    bad = tmp_path / "m.json"
    bad.write_text("{not json")
    result = run(archive, tmp_path / "o.zip", "--mapping", bad)
    assert result.exit_code == 1 and "Cannot read the mapping" in result.output


def test_a_mapping_that_is_not_an_object_is_refused(archive, tmp_path):  # noqa: F811
    bad = tmp_path / "m.json"
    bad.write_text("[1, 2]")
    assert "must be a JSON object" in run(archive, tmp_path / "o.zip", "--mapping", bad).output


def test_a_missing_target_version_refuses_and_writes_nothing(archive, tmp_path):  # noqa: F811
    mapping = tmp_path / "m.json"
    mapping.write_text(json.dumps({"template": "MONSTER", "to_version": 2}))
    out = tmp_path / "o.zip"
    result = run(archive, out, "--mapping", mapping)
    assert result.exit_code == 1 and "v2 is not in the archive" in result.output
    assert not out.exists()
