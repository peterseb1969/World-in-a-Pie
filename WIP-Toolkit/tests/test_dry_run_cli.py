"""--dry-run on prune, latest-only and strip-blobs: the real path, nothing
written, an existing output left alone. filter's are in test_filter_cli."""

import json

import pytest
from click.testing import CliRunner
from test_filter_cli import archive  # noqa: F401 — pytest fixture
from wip_toolkit.cli import main


@pytest.mark.parametrize("verb", ["prune", "latest-only", "strip-blobs"])
def test_dry_run_writes_nothing(verb, archive, tmp_path):  # noqa: F811
    out = tmp_path / "out.zip"
    out.write_bytes(b"keep")
    result = CliRunner().invoke(main, [verb, str(archive), str(out), "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["hop"]["transform"] == verb
    assert out.read_bytes() == b"keep"



def test_prune_dry_run_says_would(archive, tmp_path):  # noqa: F811
    from test_prune_cli import _with_unused_template

    src = _with_unused_template(archive, tmp_path)
    result = CliRunner().invoke(main, ["prune", str(src), str(tmp_path / "o.zip"), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would prune template aa:UNUSED" in result.output
