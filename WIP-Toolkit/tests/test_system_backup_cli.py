"""The thin `system-backup` and `system-restore` verbs: what reaches the
Registry, what is written, and what is printed. The endpoints themselves are
tested in components/registry/tests/test_case_1036_system_backup.py."""

import json
import stat
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from wip_toolkit.cli import main
from wip_toolkit.client import WIPClientError

ARCHIVE = {
    "format": "wip-system-backup", "version": 1, "created_at": "2026-11-03T00:00:00Z",
    "source": {"oidc_issuer": None, "salt_fingerprint": "sha256:abc"},
    "api_keys": [{"name": "rc-key", "key_hash": "$2b$12$x"}], "grants": [], "reservations": [],
}


def _client(response=None, error=None):
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    if error is not None:
        client.post.side_effect = error
    else:
        client.post.return_value = response
    return client


def _invoke(client, *args):
    with patch("wip_toolkit.cli.WIPClient", return_value=client):
        return CliRunner().invoke(main, list(map(str, args)))


def test_backup_writes_a_private_file(tmp_path):
    out = tmp_path / "auth.json"
    client = _client(ARCHIVE)
    result = _invoke(client, "system-backup", out)
    assert result.exit_code == 0, result.output
    assert client.post.call_args.args == ("registry", "/system-backup")
    assert json.loads(out.read_text()) == ARCHIVE
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert "1 key(s)" in result.output


def test_backup_keeps_an_existing_file_without_force(tmp_path):
    out = tmp_path / "auth.json"
    out.write_text("keep")
    result = _invoke(_client(ARCHIVE), "system-backup", out)
    assert result.exit_code == 1
    assert out.read_text() == "keep"


def test_a_refused_backup_writes_nothing(tmp_path):
    out = tmp_path / "auth.json"
    result = _invoke(_client(error=WIPClientError("HTTP 403", status_code=403)), "system-backup", out)
    assert result.exit_code == 1
    assert not out.exists()


REPORT = {
    "dry_run": True, "warnings": ["This instance already holds API keys"],
    "reservations": {"created": [], "replaced": [], "unchanged": [], "skipped": []},
    "api_keys": {"created": ["rc-key"], "replaced": [], "unchanged": [], "skipped": []},
    "grants": {"created": ["later/group:g"], "replaced": [], "unchanged": [],
               "skipped": [{"id": "x/user:a@b", "reason": "user grant from another OIDC issuer"}],
               "orphaned": ["later/group:g"]},
}


def test_restore_is_a_dry_run_unless_applied(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(ARCHIVE))
    client = _client(REPORT)
    result = _invoke(client, "system-restore", path)
    assert result.exit_code == 0, result.output
    assert client.post.call_args.args == ("registry", "/system-restore")
    assert client.post.call_args.kwargs["json"] == ARCHIVE
    assert client.post.call_args.kwargs["params"] == {
        "dry_run": "true", "overwrite": "false", "include_user_grants": "false"}
    assert "Warning:" in result.output and "created: rc-key" in result.output
    assert "user grant from another OIDC issuer" in result.output
    assert "created: later/group:g (orphaned until its namespace exists)" in result.output
    assert "Dry run" in result.output


def test_restore_passes_its_flags(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(ARCHIVE))
    client = _client({**REPORT, "dry_run": False})
    result = _invoke(client, "system-restore", path, "--apply", "--overwrite",
                     "--include-user-grants")
    assert result.exit_code == 0, result.output
    assert client.post.call_args.kwargs["params"] == {
        "dry_run": "false", "overwrite": "true", "include_user_grants": "true"}
    assert "Dry run" not in result.output


def test_a_refused_restore_exits_1(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(ARCHIVE))
    error = WIPClientError("HTTP 409: different API key hash salt", status_code=409)
    result = _invoke(_client(error=error), "system-restore", path, "--apply")
    assert result.exit_code == 1
    assert "salt" in result.output
