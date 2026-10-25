"""A Dex static user holds a list of groups.

Dex puts a static user's `groups` list into the token's groups claim, and
wip-auth reads that claim as a list, so one user can be (say) both
wip-editors and wip-admins. The spec carries `groups: list[str]`; specs saved
while it was a single `group` string still load. The user verbs take
`--group` as a comma-separated list, and `user set-groups` changes the set in
place, keeping the password.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _load_deployment, _persist_deployment, app
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers.compose_dex import render_dex_conf
from wip_deploy.secrets import ensure_secrets
from wip_deploy.secrets_backend import FileSecretBackend
from wip_deploy.spec.deployment import DexUser

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
    secrets = d.spec.secrets.model_copy(update={"location": str(tmp_path / "secrets")})
    d = d.model_copy(update={"spec": d.spec.model_copy(update={"secrets": secrets})})
    _persist_deployment(d, tmp_path, repo_root=REPO_ROOT)
    (tmp_path / "secrets").mkdir()
    return tmp_path


def _invoke(install: Path, *args: str) -> Any:
    return runner.invoke(
        app, [*args, "--install-dir", str(install), "--repo-root", str(REPO_ROOT)],
    )


class TestSpec:
    def test_single_group_spelling_loads_as_a_list(self) -> None:
        user = DexUser.model_validate(
            {"email": "e2e@wip.local", "username": "e2e", "group": "wip-editors"}
        )
        assert user.groups == ["wip-editors"]
        assert "group" not in user.model_dump()

    def test_both_spellings_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not both"):
            DexUser.model_validate(
                {"email": "a@b.c", "username": "a", "group": "x", "groups": ["y"]}
            )

    @pytest.mark.parametrize("groups", [[], [" "]])
    def test_empty_groups_rejected(self, groups: list[str]) -> None:
        with pytest.raises(ValidationError):
            DexUser(email="a@b.c", username="a", groups=groups)

    def test_duplicates_collapse_in_order(self) -> None:
        user = DexUser(email="a@b.c", username="a", groups=["x", "y", "x"])
        assert user.groups == ["x", "y"]

    def test_state_file_saved_with_single_group_loads(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        state_file = next(install.glob("deployment.deployer-state"))
        state = json.loads(state_file.read_text())
        users = state["deployment"]["spec"]["auth"]["users"]
        for u in users:
            u["group"] = u.pop("groups")[0]
        state_file.write_text(json.dumps(state))

        loaded = _load_deployment(install)
        assert [u.groups for u in loaded.spec.auth.users] == [
            ["wip-admins"], ["wip-editors"], ["wip-viewers"],
        ]


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def test_dex_config_carries_every_group(tmp_path: Path, discovery: Discovery) -> None:
    install = _install(tmp_path)
    d = _load_deployment(install)
    d.spec.auth.users.append(
        DexUser(email="e2e@wip.local", username="e2e", groups=["wip-editors", "wip-admins"])
    )
    s = ensure_secrets(d, discovery.components, discovery.apps, FileSecretBackend(tmp_path / "s"))
    conf = yaml.safe_load(render_dex_conf(d, discovery.components, discovery.apps, s))
    e2e = next(u for u in conf["staticPasswords"] if u["username"] == "e2e")
    assert e2e["groups"] == ["wip-editors", "wip-admins"]


class TestUserAdd:
    def test_comma_separated_groups(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        (install / "secrets" / "dex-password-e2e").write_text("pw\n")
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(
                install, "user", "add", "e2e", "--email", "e2e@wip.local",
                "--group", "wip-editors, wip-admins",
            )
        assert result.exit_code == 0, result.output
        users = mutate.call_args.args[0].spec.auth.users
        assert next(u for u in users if u.username == "e2e").groups == [
            "wip-editors", "wip-admins",
        ]
        assert "wip-editors,wip-admins" in result.output

    def test_no_group_refused(self, tmp_path: Path) -> None:
        result = _invoke(
            _install(tmp_path), "user", "add", "e2e", "--email", "e2e@wip.local",
            "--group", " , ",
        )
        assert result.exit_code == 2
        assert "at least one group" in result.output

    def test_existing_user_points_at_set_groups(self, tmp_path: Path) -> None:
        result = _invoke(
            _install(tmp_path), "user", "add", "editor", "--email", "x@wip.local",
            "--group", "wip-admins",
        )
        assert result.exit_code == 2
        assert "set-groups" in result.output


class TestUserSetGroups:
    def test_replaces_groups_and_keeps_the_password(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        secret = install / "secrets" / "dex-password-editor"
        secret.write_text("pw\n")
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(
                install, "user", "set-groups", "editor", "--group", "wip-editors,wip-admins",
            )
        assert result.exit_code == 0, result.output
        users = mutate.call_args.args[0].spec.auth.users
        assert [u.username for u in users] == ["admin", "editor", "viewer"]
        assert users[1].groups == ["wip-editors", "wip-admins"]
        assert secret.read_text() == "pw\n"

    def test_unknown_user_refused(self, tmp_path: Path) -> None:
        result = _invoke(_install(tmp_path), "user", "set-groups", "ghost", "--group", "x")
        assert result.exit_code == 2
        assert "not a spec-declared user" in result.output

    def test_last_admin_keeps_wip_admins(self, tmp_path: Path) -> None:
        result = _invoke(
            _install(tmp_path), "user", "set-groups", "admin", "--group", "wip-editors",
        )
        assert result.exit_code == 2
        assert "last wip-admins user" in result.output

    def test_admin_may_be_removed_while_another_admin_remains(self, tmp_path: Path) -> None:
        install = _install(tmp_path)
        d = _load_deployment(install)
        d.spec.auth.users[1].groups = ["wip-admins"]
        _persist_deployment(d, install, repo_root=REPO_ROOT)
        with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
            result = _invoke(install, "user", "set-groups", "admin", "--group", "wip-viewers")
        assert result.exit_code == 0, result.output
        assert mutate.call_args.args[0].spec.auth.users[0].groups == ["wip-viewers"]


def test_remove_counts_an_admin_in_a_multi_group_user(tmp_path: Path) -> None:
    install = _install(tmp_path)
    d = _load_deployment(install)
    d.spec.auth.users[1].groups = ["wip-editors", "wip-admins"]
    _persist_deployment(d, install, repo_root=REPO_ROOT)
    with patch("wip_deploy.cli._apply_and_persist_mutation"):
        result = _invoke(install, "user", "remove", "admin")
    assert result.exit_code == 0, result.output
