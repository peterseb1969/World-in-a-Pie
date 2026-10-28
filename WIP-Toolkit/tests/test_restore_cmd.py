"""Tests for the thin `restore` verb: what reaches the server, and how the
job is followed."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from wip_toolkit.cli import main
from wip_toolkit.restore import build_form, wait_for_job


def _mock_client(final_status="complete"):
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.post_form.return_value = {"job_id": "rst-1", "status": "pending"}
    client.get.return_value = {"job_id": "rst-1", "status": final_status}
    return client


def _archive(tmp_path):
    path = tmp_path / "b.zip"
    path.write_bytes(b"PK\x03\x04")
    return path


def _invoke(tmp_path, client, *args):
    with (
        patch("wip_toolkit.cli.WIPClient", return_value=client),
        patch("wip_toolkit.cli.ArchiveReader") as reader,
    ):
        reader.return_value.__enter__.return_value.read_manifest.return_value.namespace = "aa"
        return CliRunner().invoke(main, ["restore", str(_archive(tmp_path)), *args])


def _form(client):
    return client.post_form.call_args.kwargs["data"]


class TestBuildForm:
    def test_merge_carries_older_versions_defaulting_to_retire(self):
        form = build_form("merge", on_clash="overwrite")
        assert form["on_clash"] == "overwrite"
        assert form["older_versions"] == "retire"

    def test_other_modes_send_no_merge_policy(self):
        form = build_form("restore")
        assert not {"on_clash", "older_versions", "add_missing"} & form.keys()


class TestRestoreVerb:
    def test_merge_sends_the_choice_and_follows_the_job(self, tmp_path):
        client = _mock_client()
        result = _invoke(
            tmp_path, client,
            "--mode", "merge", "--on-clash", "newer", "--older-versions", "keep",
            "--extend-terminologies",
        )

        assert result.exit_code == 0, result.output
        args = client.post_form.call_args.args
        assert args[1] == "/backup/namespaces/aa/restore"
        form = _form(client)
        assert form["mode"] == "merge"
        assert form["on_clash"] == "newer"
        assert form["older_versions"] == "keep"
        assert form["extend_terminologies"] == "true"
        client.get.assert_called_with("document-store", "/backup/jobs/rst-1")

    def test_merge_default_retires_older_versions(self, tmp_path):
        client = _mock_client()
        result = _invoke(tmp_path, client, "--mode", "merge", "--on-clash", "overwrite")
        assert result.exit_code == 0, result.output
        assert _form(client)["older_versions"] == "retire"

    def test_a_merge_option_outside_merge_is_a_usage_error(self, tmp_path):
        client = _mock_client()
        result = _invoke(tmp_path, client, "--older-versions", "keep")
        assert result.exit_code == 2
        assert "--older-versions" in result.output
        client.post_form.assert_not_called()

    def test_a_failed_job_exits_1(self, tmp_path):
        client = _mock_client(final_status="failed")
        result = _invoke(tmp_path, client)
        assert result.exit_code == 1

    def test_a_refused_request_is_reported_not_raised(self, tmp_path):
        from wip_toolkit.client import WIPClientError

        client = _mock_client()
        client.post_form.side_effect = WIPClientError(
            "HTTP 400", status_code=400, response_body={"detail": "no"}
        )
        result = _invoke(tmp_path, client)
        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)

    def test_no_wait_returns_the_queued_job(self, tmp_path):
        client = _mock_client()
        result = _invoke(tmp_path, client, "--no-wait")
        assert result.exit_code == 0, result.output
        client.get.assert_not_called()

    def test_fresh_map_is_sent_as_json(self, tmp_path):
        client = _mock_client()
        result = _invoke(tmp_path, client, "--mode", "fresh", "--map", "aa=bb")
        assert result.exit_code == 0, result.output
        assert _form(client)["namespace_map"] == '{"aa": "bb"}'


def test_wait_stops_at_the_timeout():
    client = MagicMock()
    client.get.return_value = {"job_id": "rst-1", "status": "running"}
    ticks = iter([0.0, 0.5, 2.0])

    snapshot = wait_for_job(
        client, "rst-1", timeout=1.0, sleep=lambda _s: None, clock=lambda: next(ticks)
    )

    assert snapshot["status"] == "running"
    assert client.get.call_count == 2
