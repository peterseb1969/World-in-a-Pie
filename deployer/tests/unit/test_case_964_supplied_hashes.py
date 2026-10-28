"""CASE-964 — Dex users declared with an existing bcrypt hash.

A user brought over from a hand-written Dex config must keep their
password across every re-render. `DexUser.password = "supplied"` declares
that: only the hash secret exists, it is operator-supplied (never
generated), and the render uses it as it is. `user add
--password-hash-file` declares one such user; `user import` declares many
from a Dex config or a CSV file, in one apply.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import bcrypt
import pytest
import yaml
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _persist_deployment, app
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers.compose_dex import render_dex_conf
from wip_deploy.secrets import (
    OperatorSecretMissing,
    collect_required_secrets,
    ensure_secrets,
    is_bcrypt_hash,
)
from wip_deploy.secrets_backend import FileSecretBackend
from wip_deploy.spec import DexUser

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


def _hash(password: str = "pw") -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(tmp_path: Path, *extra: DexUser):
    d = build_deployment(
        BuildInputs(
            target="compose", variant="dev", hostname="localhost",
            tls="internal", compose_data_dir=tmp_path / "data",
        )
    )
    secrets = d.spec.secrets.model_copy(update={"location": str(tmp_path / "secrets")})
    auth = d.spec.auth.model_copy(update={"users": [*d.spec.auth.users, *extra]})
    spec = d.spec.model_copy(update={"secrets": secrets, "auth": auth})
    return d.model_copy(update={"spec": spec})


def _install(tmp_path: Path) -> Path:
    _persist_deployment(_deployment(tmp_path), tmp_path, repo_root=REPO_ROOT)
    return tmp_path


def _invoke(install: Path, *args: str):
    return runner.invoke(
        app, [*args, "--install-dir", str(install), "--repo-root", str(REPO_ROOT)]
    )


ALICE = DexUser(email="alice@example.com", username="alice", groups=["wip-editors"],
                password="supplied")


class TestSpec:
    def test_password_defaults_to_generated(self) -> None:
        user = DexUser(email="a@example.com", username="a", groups=["wip-viewers"])
        assert user.password == "generated"

    def test_unknown_source_rejected(self) -> None:
        with pytest.raises(ValueError):
            DexUser(email="a@example.com", username="a", groups=["g"], password="typed")  # type: ignore[arg-type]

    def test_bcrypt_shape(self) -> None:
        assert is_bcrypt_hash(_hash())
        assert not is_bcrypt_hash("hunter2")
        assert not is_bcrypt_hash(_hash()[:-1])


class TestSecrets:
    def test_supplied_user_has_only_its_hash(self, tmp_path: Path, discovery: Discovery) -> None:
        names = collect_required_secrets(
            _deployment(tmp_path, ALICE), discovery.components, discovery.apps
        )
        assert "dex-password-alice.bcrypt" in names
        assert "dex-password-alice" not in names
        # Generated users keep the plaintext + hash pair.
        assert {"dex-password-admin", "dex-password-admin.bcrypt"} <= names

    def test_missing_hash_stops_the_apply_and_is_not_invented(
        self, tmp_path: Path, discovery: Discovery
    ) -> None:
        backend = FileSecretBackend(tmp_path / "secrets")
        with pytest.raises(OperatorSecretMissing, match="user import"):
            ensure_secrets(
                _deployment(tmp_path, ALICE), discovery.components, discovery.apps, backend
            )
        assert not (tmp_path / "secrets" / "dex-password-alice.bcrypt").exists()

    def test_supplied_hash_is_rendered_as_it_is(
        self, tmp_path: Path, discovery: Discovery
    ) -> None:
        h = _hash("alices-password")
        (tmp_path / "secrets").mkdir()
        (tmp_path / "secrets" / "dex-password-alice.bcrypt").write_text(h)
        d = _deployment(tmp_path, ALICE)
        resolved = ensure_secrets(
            d, discovery.components, discovery.apps, FileSecretBackend(tmp_path / "secrets")
        )
        conf = render_dex_conf(d, discovery.components, discovery.apps, resolved)
        assert conf is not None
        rendered = yaml.safe_load(conf) if isinstance(conf, str) else conf
        alice = next(u for u in rendered["staticPasswords"] if u["username"] == "alice")
        assert alice["hash"] == h
        assert alice["userID"] == "alice-001"
        assert not (tmp_path / "secrets" / "dex-password-alice").exists()


class TestUserAddWithHash:
    def test_declares_a_supplied_user_and_stores_the_hash(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        h = _hash()
        hash_file = tmp_path / "alice.hash"
        hash_file.write_text(h + "\n")

        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(
                install, "user", "add", "alice", "--email", "alice@example.com",
                "--group", "wip-editors", "--password-hash-file", str(hash_file),
            )
        assert result.exit_code == 0, result.output
        assert mutate.call_count == 1
        added = next(u for u in mutate.call_args.args[0].spec.auth.users if u.username == "alice")
        assert added.password == "supplied"
        assert (tmp_path / "secrets" / "dex-password-alice.bcrypt").read_text() == h
        assert "shown once" not in result.output

    def test_a_plaintext_is_refused(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        bad = tmp_path / "pw.txt"
        bad.write_text("hunter2")
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(
                install, "user", "add", "alice", "--email", "alice@example.com",
                "--group", "wip-editors", "--password-hash-file", str(bad),
            )
        assert result.exit_code == 2
        assert "bcrypt" in result.output
        mutate.assert_not_called()


def _dex_config(tmp_path: Path, *users: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"issuer": "https://x/dex", "staticPasswords": list(users)}))
    return path


def _entry(username: str, *, email: str | None = None, user_id: str | None = None) -> dict:
    return {
        "email": email or f"{username}@example.com",
        "hash": _hash(username),
        "username": username,
        "userID": user_id or f"{username}-001",
    }


class TestUserImport:
    def test_dex_config_in_one_apply_with_group_precedence(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        config = _dex_config(
            tmp_path,
            _entry("admin", email="admin@wip.local"),  # already declared: skipped
            _entry("alice"),
            _entry("bob"),
        )
        groups = tmp_path / "groups.yaml"
        groups.write_text("alice: [wip-admins, wip-editors]\n")

        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(
                install, "user", "import", str(config),
                "--groups-file", str(groups), "--group", "wip-viewers",
            )
        assert result.exit_code == 0, result.output
        assert mutate.call_count == 1  # one apply for every user
        users = {u.username: u for u in mutate.call_args.args[0].spec.auth.users}
        assert users["alice"].groups == ["wip-admins", "wip-editors"]  # groups file wins
        assert users["bob"].groups == ["wip-viewers"]                   # --group fallback
        assert users["alice"].password == users["bob"].password == "supplied"
        assert users["admin"].password == "generated"                   # left as it was
        assert "Already declared" in result.output and "admin" in result.output
        for name in ("alice", "bob"):
            assert is_bcrypt_hash((tmp_path / "secrets" / f"dex-password-{name}.bcrypt").read_text())

    def test_csv_groups_column_wins(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        csv_file = tmp_path / "users.csv"
        csv_file.write_text(
            "username,email,hash,groups\n"
            f'carol,carol@example.com,{_hash()},"wip-editors,app-x"\n'
            f"dave,dave@example.com,{_hash()},\n"
        )
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(install, "user", "import", str(csv_file), "--group", "wip-viewers")
        assert result.exit_code == 0, result.output
        users = {u.username: u for u in mutate.call_args.args[0].spec.auth.users}
        assert users["carol"].groups == ["wip-editors", "app-x"]
        assert users["dave"].groups == ["wip-viewers"]

    def test_any_problem_refuses_the_whole_import(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        bad = _entry("erin")
        bad["hash"] = "not-a-hash"
        config = _dex_config(tmp_path, _entry("alice"), bad, _entry("frank", email="admin@wip.local"))
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(install, "user", "import", str(config), "--group", "wip-viewers")
        assert result.exit_code == 2
        assert "not a bcrypt hash" in result.output
        assert "already used by 'admin'" in result.output
        mutate.assert_not_called()
        assert not (tmp_path / "secrets" / "dex-password-alice.bcrypt").exists()

    def test_a_user_without_groups_is_named(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(install, "user", "import", str(_dex_config(tmp_path, _entry("alice"))))
        assert result.exit_code == 2
        assert "alice" in result.output and "no groups" in result.output
        mutate.assert_not_called()

    def test_dry_run_changes_nothing(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        config = _dex_config(tmp_path, _entry("alice"))
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(
                install, "user", "import", str(config), "--group", "wip-viewers", "--dry-run"
            )
        assert result.exit_code == 0, result.output
        assert "would be declared" in result.output
        mutate.assert_not_called()
        assert not (tmp_path / "secrets" / "dex-password-alice.bcrypt").exists()

    def test_a_failed_apply_removes_the_stored_hashes(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        config = _dex_config(tmp_path, _entry("alice"))
        with patch(
            "wip_deploy.cli._apply_and_persist_mutation", side_effect=RuntimeError("apply failed")
        ):
            result = _invoke(install, "user", "import", str(config), "--group", "wip-viewers")
        assert result.exit_code != 0
        assert not (tmp_path / "secrets" / "dex-password-alice.bcrypt").exists()

    def test_a_changed_user_id_is_warned(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        config = _dex_config(tmp_path, _entry("alice", user_id="0815"))
        with patch("wip_deploy.cli._apply_and_persist_mutation"):
            result = _invoke(install, "user", "import", str(config), "--group", "wip-viewers")
        assert result.exit_code == 0, result.output
        assert "0815" in result.output and "subject changes" in result.output
