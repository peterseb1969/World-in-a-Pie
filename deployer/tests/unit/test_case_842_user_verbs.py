"""CASE-842 — `wip-deploy user add|remove|list`.

Dex static users were spec configuration without an operator write path:
`auth.users` existed, persisted, and rendered with stable per-user
password secrets, but the only way to add a user was editing the RENDERED
dex config.yaml — which the next render correctly overwrote. These verbs
are that write path, mirroring --api-key / rotate-key.

The happy paths reuse the already-tested `_apply_and_persist_mutation`
machinery (mocked here, same as the redeploy tests); the unit surface is
the guards plus the exact spec mutation each verb hands to the apply.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _persist_deployment, app
from wip_deploy.secrets import bcrypt_secret_name

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


def _install(tmp_path: Path) -> Path:
    d = build_deployment(
        BuildInputs(
            target="compose",
            variant="dev",
            hostname="localhost",
            tls="internal",
            compose_data_dir=tmp_path / "data",
        )
    )
    secrets = d.spec.secrets.model_copy(
        update={"location": str(tmp_path / "secrets")}
    )
    spec = d.spec.model_copy(update={"secrets": secrets})
    d = d.model_copy(update={"spec": spec})
    _persist_deployment(d, tmp_path, repo_root=REPO_ROOT)
    return tmp_path


def _invoke(install: Path, *args: str):
    return runner.invoke(
        app,
        [*args, "--install-dir", str(install), "--repo-root", str(REPO_ROOT)],
    )


class TestUserAddGuards:
    def test_unsafe_username_refused(self, tmp_path: Path) -> None:
        result = _invoke(
            _install(tmp_path),
            "user", "add", "../escape",
            "--email", "x@example.com", "--group", "wip-viewers",
        )
        assert result.exit_code == 2
        assert "username" in result.output

    def test_duplicate_username_refused(self, tmp_path: Path) -> None:
        # "admin" is one of the three default spec users.
        result = _invoke(
            _install(tmp_path),
            "user", "add", "admin",
            "--email", "someone@example.com", "--group", "wip-admins",
        )
        assert result.exit_code == 2
        assert "already declared" in result.output

    def test_duplicate_email_refused(self, tmp_path: Path) -> None:
        result = _invoke(
            _install(tmp_path),
            "user", "add", "admin2",
            "--email", "admin@wip.local", "--group", "wip-admins",
        )
        assert result.exit_code == 2
        assert "admin" in result.output  # names the clashing user

    def test_invalid_email_refused(self, tmp_path: Path) -> None:
        result = _invoke(
            _install(tmp_path),
            "user", "add", "peter",
            "--email", "not-an-email", "--group", "wip-viewers",
        )
        assert result.exit_code == 2
        assert "email" in result.output


class TestUserAddHappyPath:
    def test_appends_user_and_prints_password_once(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        # The real apply generates the password secret; the mock does not,
        # so pre-create what ensure_secrets would have written.
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "dex-password-peter").write_text("s3cret-pw\n")

        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(
                install,
                "user", "add", "peter",
                "--email", "peter@example.com", "--group", "wip-admins",
            )
        assert result.exit_code == 0, result.output
        assert mutate.call_count == 1

        deployment = mutate.call_args.args[0]
        added = [u for u in deployment.spec.auth.users if u.username == "peter"]
        assert len(added) == 1
        assert added[0].email == "peter@example.com"
        assert added[0].group == "wip-admins"
        # The three defaults are still there — add is additive.
        assert len(deployment.spec.auth.users) == 4
        assert "Added Dex user 'peter'" in mutate.call_args.args[4]

        assert "s3cret-pw" in result.output
        assert "shown once" in result.output


class TestUserRemove:
    def test_unknown_user_refused(self, tmp_path: Path) -> None:
        result = _invoke(_install(tmp_path), "user", "remove", "ghost")
        assert result.exit_code == 2
        # Names the declared users so the operator can pick the right one.
        assert "admin" in result.output
        assert "viewer" in result.output

    def test_last_admin_refused(self, tmp_path: Path) -> None:
        # The default spec has exactly one wip-admins user.
        result = _invoke(_install(tmp_path), "user", "remove", "admin")
        assert result.exit_code == 2
        assert "last wip-admins user" in result.output

    def test_removes_user_and_its_secrets(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        secret = secrets_dir / "dex-password-viewer"
        secret.write_text("pw\n")
        hash_cache = secrets_dir / bcrypt_secret_name("dex-password-viewer")
        hash_cache.write_text("hash\n")

        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(install, "user", "remove", "viewer")
        assert result.exit_code == 0, result.output

        deployment = mutate.call_args.args[0]
        names = [u.username for u in deployment.spec.auth.users]
        assert "viewer" not in names
        assert names == ["admin", "editor"]
        assert "Removed Dex user 'viewer'" in mutate.call_args.args[4]

        # Secret + derived bcrypt hash deleted, so a re-add mints fresh.
        assert not secret.exists()
        assert not hash_cache.exists()

    def test_failed_apply_keeps_secrets(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        secret = secrets_dir / "dex-password-viewer"
        secret.write_text("pw\n")

        import typer

        with patch(
            "wip_deploy.cli._apply_and_persist_mutation",
            side_effect=typer.Exit(1),
        ):
            result = _invoke(install, "user", "remove", "viewer")
        assert result.exit_code == 1
        assert secret.exists()


class TestUserList:
    def test_lists_defaults_with_secret_state(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        (secrets_dir / "dex-password-admin").write_text("pw\n")

        result = runner.invoke(
            app, ["user", "list", "--install-dir", str(install)]
        )
        assert result.exit_code == 0, result.output
        for username in ("admin", "editor", "viewer"):
            assert username in result.output
        assert "admin@wip.local" in result.output
        assert "wip-admins" in result.output
        # admin's secret exists; the others are pending an apply.
        assert "not yet generated" in result.output
