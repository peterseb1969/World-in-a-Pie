"""Tests for the thin `restore` verb: what reaches the server, and how the
job is followed."""

import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from wip_archive.model import Finding
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


def _model(findings=()):
    """A verified archive model: namespace 'aa', the given findings."""
    model = MagicMock()
    model.manifest.namespace = "aa"
    model.findings.return_value = list(findings)
    return model


def _invoke(tmp_path, client, *args, findings=()):
    with (
        patch("wip_toolkit.cli.WIPClient", return_value=client),
        patch("wip_toolkit.verify.verify_archive", return_value=_model(findings)),
    ):
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

    def test_a_complete_job_exits_0(self, tmp_path):
        result = _invoke(tmp_path, _mock_client(final_status="complete"))
        assert result.exit_code == 0, result.output

    def test_a_job_still_running_at_the_timeout_exits_1(self, tmp_path):
        client = _mock_client(final_status="running")
        with patch("wip_toolkit.restore.wait_for_job",
                   return_value={"job_id": "rst-1", "status": "running"}) as wait:
            result = _invoke(tmp_path, client, "--timeout", "5")
        assert wait.call_args.kwargs["timeout"] == 5.0
        assert result.exit_code == 1
        assert "rst-1: not finished within --timeout" in result.output

    def test_json_still_prints_the_unfinished_snapshot(self, tmp_path):
        client = _mock_client(final_status="running")
        with patch("wip_toolkit.restore.wait_for_job",
                   return_value={"job_id": "rst-1", "status": "running"}):
            result = _invoke(tmp_path, client, "--json")
        assert result.exit_code == 1
        assert '"status": "running"' in result.output

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

    def test_allow_missing_identity_reaches_the_form(self, tmp_path):
        client = _mock_client()
        result = _invoke(tmp_path, client, "--allow-missing-identity")
        assert result.exit_code == 0, result.output
        assert _form(client)["allow_missing_identity"] == "true"

    def test_without_the_flag_missing_identity_is_not_accepted(self, tmp_path):
        client = _mock_client()
        result = _invoke(tmp_path, client)
        assert result.exit_code == 0, result.output
        assert _form(client)["allow_missing_identity"] == "false"

    def test_allow_missing_identity_is_refused_outside_restore(self, tmp_path):
        client = _mock_client()
        result = _invoke(tmp_path, client, "--mode", "merge", "--allow-missing-identity")
        assert result.exit_code == 2
        assert "--mode restore only" in result.output
        client.post_form.assert_not_called()

    def test_force_accepts_a_failed_verify(self, tmp_path):
        client = _mock_client()
        result = _invoke(tmp_path, client, "--force")
        assert result.exit_code == 0, result.output
        assert _form(client)["allow_failed_verify"] == "true"

    def test_without_force_a_failed_verify_is_not_accepted(self, tmp_path):
        client = _mock_client()
        result = _invoke(tmp_path, client)
        assert result.exit_code == 0, result.output
        assert _form(client)["allow_failed_verify"] == "false"

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


class TestVerifyGate:
    """restore verifies the archive offline before uploading it."""

    ERROR = Finding("error", "template_version_missing", "documents", "pinned to v3", 2)
    WARNING = Finding("warning", "document_invalid", "documents", "fails its schema", 1)

    def test_an_error_refuses_the_upload(self, tmp_path):
        client = _mock_client()
        result = _invoke(tmp_path, client, findings=[self.ERROR])
        assert result.exit_code == 1
        assert "not uploaded" in result.output
        assert "template_version_missing" in result.output
        client.post_form.assert_not_called()

    def test_force_uploads_and_passes_the_server_gate_too(self, tmp_path):
        client = _mock_client()
        result = _invoke(tmp_path, client, "--force", findings=[self.ERROR])
        assert result.exit_code == 0, result.output
        assert "--force" in result.output
        assert _form(client)["allow_failed_verify"] in (True, "true")

    def test_warnings_print_and_the_upload_continues(self, tmp_path):
        client = _mock_client()
        result = _invoke(tmp_path, client, findings=[self.WARNING])
        assert result.exit_code == 0, result.output
        assert "document_invalid" in result.output
        client.post_form.assert_called_once()

    def test_json_refusal_is_machine_readable(self, tmp_path):
        client = _mock_client()
        result = _invoke(tmp_path, client, "--json", findings=[self.ERROR])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["uploaded"] is False and payload["verdict"] == "fail"

    def test_a_real_broken_archive_is_refused(self, tmp_path):
        """The gate on a real archive, not a mocked model."""
        path = _real_archive(tmp_path, template_version=3)
        client = _mock_client()
        with patch("wip_toolkit.cli.WIPClient", return_value=client):
            result = CliRunner().invoke(main, ["restore", str(path)])
        assert result.exit_code == 1, result.output
        assert "template_version_missing" in result.output
        client.post_form.assert_not_called()

    def test_a_real_clean_archive_uploads(self, tmp_path):
        path = _real_archive(tmp_path, template_version=1)
        client = _mock_client()
        with patch("wip_toolkit.cli.WIPClient", return_value=client):
            result = CliRunner().invoke(main, ["restore", str(path)])
        assert result.exit_code == 0, result.output
        client.post_form.assert_called_once()


def _real_archive(tmp_path, *, template_version, data=None):
    """One template v1 and one document pinned to `template_version`."""
    from wip_archive.archive import ArchiveWriter
    from wip_archive.identity import compute_composite_key_hash
    from wip_archive.models import EntityCounts, Manifest, NamespaceEntry

    tpl = "0190c000-0000-7000-0000-0000000000t1"
    rows = {
        "templates": [{"template_id": tpl, "version": 1, "value": "ITEM", "namespace": "aa",
                       "status": "active", "identity_fields": ["code"],
                       "fields": [{"name": "code", "type": "string", "mandatory": True}]}],
        "documents": [{"document_id": "d1", "namespace": "aa", "template_id": tpl,
                       "template_version": template_version, "version": 1, "status": "active",
                       "identity_hash": compute_composite_key_hash({"code": "A"}),
                       "data": data or {"code": "A"}, "term_references": [], "references": [],
                       "file_references": []}],
        "registry_entries": [
            {"entry_id": i, "namespace": "aa", "entity_type": t, "primary_composite_key": {},
             "primary_composite_key_hash": "h", "synonyms": [], "status": "active"}
            for i, t in ((tpl, "templates"), ("d1", "documents"))
        ],
    }
    path = tmp_path / "real.zip"
    writer = ArchiveWriter(path, default_namespace="aa")
    for entity_type, entities in rows.items():
        for entity in entities:
            writer.add_entity(entity_type, entity, namespace="aa")
    counts = EntityCounts(**{k: len(v) for k, v in rows.items()})
    writer.write(Manifest(namespace="aa", counts=counts,
                          namespaces=[NamespaceEntry(prefix="aa", counts=counts)]))
    return path
