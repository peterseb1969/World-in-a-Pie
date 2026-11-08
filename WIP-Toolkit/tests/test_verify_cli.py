"""`wip-toolkit verify`: the archive checked offline, reduced to a verdict."""

import json

from click.testing import CliRunner
from test_restore_cmd import _real_archive
from wip_toolkit.cli import main


def run(*args):
    return CliRunner().invoke(main, ["verify", *map(str, args)])


def test_a_clean_archive_passes(tmp_path):
    result = run(_real_archive(tmp_path, template_version=1))
    assert result.exit_code == 0, result.output
    assert "verdict: pass" in result.output
    assert "1 document row(s) checked, 0 invalid" in result.output


def test_an_error_fails_with_exit_1(tmp_path):
    result = run(_real_archive(tmp_path, template_version=3))
    assert result.exit_code == 1
    # The findings table wraps long class names; --json pins the class exactly.
    assert "template_ver" in result.output
    assert "verdict: fail" in result.output


def test_warnings_pass_unless_strict(tmp_path):
    path = _real_archive(tmp_path, template_version=1, data={"code": "A", "extra": 1})
    lenient = run(path)
    assert lenient.exit_code == 0, lenient.output
    assert "verdict: warn" in lenient.output and "document_invalid" in lenient.output
    assert run(path, "--strict").exit_code == 1


def test_json_carries_verdict_findings_and_document_checks(tmp_path):
    result = run(_real_archive(tmp_path, template_version=3), "--json")
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["schema"] == "1.3"
    assert payload["verdict"] == "fail"
    assert payload["counts"]["error"] >= 1
    assert {f["class"] for f in payload["findings"]} >= {"template_version_missing"}
    assert payload["document_checks"]["documents_checked"] == 1


def test_a_missing_archive_exits_1(tmp_path):
    result = run(tmp_path / "nope.zip")
    assert result.exit_code == 1 and "not found" in result.output.lower()
