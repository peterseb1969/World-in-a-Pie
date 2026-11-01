"""The thin `backup` verb: what reaches the server, how the job is followed,
and when nothing is written. The real endpoint is exercised in
tests/integration/test_backup_verb.py."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from wip_toolkit.cli import main


def _client(final_status="complete", archive=b"PK-archive"):
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.post.return_value = {"job_id": "bkp-1", "status": "pending"}
    client.get.return_value = {"job_id": "bkp-1", "status": final_status, "error": "boom"}
    client.stream_to_file.side_effect = lambda _svc, _path, fh: fh.write(archive)
    return client


def _invoke(client, *args):
    with patch("wip_toolkit.cli.WIPClient", return_value=client):
        return CliRunner().invoke(main, ["backup", *map(str, args)])


def test_starts_follows_and_downloads(tmp_path):
    client = _client()
    out = tmp_path / "lib.zip"
    result = _invoke(client, "library", out, "--also", "kb", "--include-files")
    assert result.exit_code == 0, result.output
    path = client.post.call_args.args[1]
    body = client.post.call_args.kwargs["json"]
    assert path == "/backup/namespaces/library/backup"
    assert body == {"all_namespaces": False, "include_files": True,
                    "skip_documents": False, "namespaces": ["kb"]}
    assert client.stream_to_file.call_args.args[1] == "/backup/jobs/bkp-1/download"
    assert out.read_bytes() == b"PK-archive"
    assert "bkp-1: complete" in result.output


def test_a_failed_job_writes_nothing_and_exits_1(tmp_path):
    out = tmp_path / "lib.zip"
    result = _invoke(_client("failed"), "library", out)
    assert result.exit_code == 1
    assert "boom" in result.output
    assert not out.exists()


def test_json_reports_the_output(tmp_path):
    out = tmp_path / "lib.zip"
    result = _invoke(_client(), "library", out, "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["output"] == str(out) and payload["bytes"] == len(b"PK-archive")


def test_an_existing_output_needs_force(tmp_path):
    out = tmp_path / "lib.zip"
    out.write_bytes(b"keep")
    client = _client()
    assert _invoke(client, "library", out).exit_code == 1
    assert out.read_bytes() == b"keep"
    client.post.assert_not_called()
    assert _invoke(_client(), "library", out, "--force").exit_code == 0


def test_export_warns_that_it_is_deprecated():
    client = _client()
    client.check_all_services.return_value = {}
    with patch("wip_toolkit.cli.WIPClient", return_value=client), \
            patch("wip_toolkit.cli.run_export"):
        result = CliRunner().invoke(main, ["export", "library", "out.zip"])
    assert "Deprecated" in result.output
    assert "wip-toolkit backup" in result.output and "filter" in result.output
