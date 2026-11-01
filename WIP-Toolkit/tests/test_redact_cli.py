"""`wip-toolkit redact`: --field parsing, refusals, and no values in the hop."""

import json

from click.testing import CliRunner
from test_filter_cli import archive  # noqa: F401 — pytest fixture
from wip_archive.archive import ArchiveReader
from wip_toolkit.cli import main


def run(*args):
    return CliRunner().invoke(main, ["redact", *map(str, args)])


def test_a_field_is_cleared_and_only_its_path_recorded(archive, tmp_path):  # noqa: F811
    out = tmp_path / "out.zip"
    result = run(archive, out, "--field", "MONSTER=favourite", "--json")
    assert result.exit_code == 0, result.output
    hop = json.loads(result.output)["hop"]
    assert hop["params_redacted"] is True
    assert hop["params"] == {"fields": {"aa:MONSTER": ["favourite"]}}
    with ArchiveReader(out) as reader:
        monster = next(d for d in reader.read_entities("documents", namespace="aa")
                       if d["template_id"].endswith("m1"))
    assert monster["data"]["favourite"] is None and monster["references"] == []


def test_an_identity_field_is_refused(archive, tmp_path):  # noqa: F811
    result = run(archive, tmp_path / "out.zip", "--field", "MONSTER=name")
    assert result.exit_code == 1 and "identity field" in result.output


def test_a_malformed_field_is_a_usage_error(archive, tmp_path):  # noqa: F811
    assert run(archive, tmp_path / "out.zip", "--field", "MONSTER").exit_code == 2
