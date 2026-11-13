"""unregister-app checks the name it deletes by; validate-manifest applies
the install's rules a manifest can be judged by alone.

1. `unregister_app` joined an unchecked name into a path and unlinked it, so
   `unregister-app ../prod/docker-compose` deleted an install's compose file
   (and an absolute name any `.yaml` the operator could write). The name is now
   checked as `register_app` checks it, and the verb exits 2 with the reason.
2. `validate-manifest` called itself the canonical answer but skipped the
   install's app-name-vs-component rule, and refused `from_component` /
   `depends_on` naming an app, which the install accepts. Both now match, and
   its output names the checks that need an install.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from wip_deploy.app_registry import AppRegistryError, register_app, unregister_app
from wip_deploy.cli import app as cli_app
from wip_deploy.validate_manifest import validate_manifest

from .test_case_353 import _valid_manifest_body, _write_manifest

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[3]


# ── 1. unregister_app ────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["../prod/docker-compose", "/etc/hosts-backup", "a/b", "Upper"])
def test_a_path_like_name_is_refused_before_anything_is_deleted(tmp_path: Path, name: str) -> None:
    registry = tmp_path / "apps"
    registry.mkdir()
    victim = tmp_path / "prod" / "docker-compose.yaml"
    victim.parent.mkdir()
    victim.write_text("services: {}\n")
    with pytest.raises(AppRegistryError, match="must match"):
        unregister_app(name, directory=registry)
    assert victim.exists()


def test_a_valid_name_still_unregisters(tmp_path: Path) -> None:
    registry = tmp_path / "apps"
    register_app("wip-kb", tmp_path, directory=registry)
    assert unregister_app("wip-kb", directory=registry) is True
    assert unregister_app("wip-kb", directory=registry) is False


def test_the_verb_exits_2_with_the_reason() -> None:
    r = runner.invoke(cli_app, ["unregister-app", "../prod/docker-compose"])
    assert r.exit_code == 2
    assert "must match" in r.output


# ── 2. validate_manifest ─────────────────────────────────────────────────


def _validate(tmp_path: Path, body: dict) -> list[str]:
    _app, errors = validate_manifest(_write_manifest(tmp_path / "wip-app.yaml", body), REPO_ROOT)
    return [e.format() for e in errors]


@pytest.mark.parametrize("name,what", [("registry", "a component"), ("caddy", "the front door")])
def test_an_app_named_like_a_component_is_refused(tmp_path: Path, name: str, what: str) -> None:
    body = _valid_manifest_body(name)
    body["app_metadata"]["label"] = f"{name}-ui"
    errors = _validate(tmp_path, body)
    assert any(f"is the name of {what}" in e for e in errors), errors


def test_an_app_may_be_named_as_a_reference_target(tmp_path: Path) -> None:
    body = _valid_manifest_body("ref-app")
    body["spec"]["depends_on"] = ["react-console"]
    body["spec"]["env"]["required"].append(
        {"name": "CONSOLE_URL", "source": {"from_component": "react-console"}}
    )
    assert _validate(tmp_path, body) == []


def test_an_unknown_target_is_still_refused(tmp_path: Path) -> None:
    body = _valid_manifest_body("ref-app")
    body["spec"]["depends_on"] = ["no-such-thing"]
    errors = _validate(tmp_path, body)
    assert any("undiscovered component or app 'no-such-thing'" in e for e in errors), errors


def test_a_pass_names_the_checks_that_need_an_install(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path / "wip-app.yaml", _valid_manifest_body("solo-app"))
    r = runner.invoke(cli_app, ["validate-manifest", str(path), "--repo-root", str(REPO_ROOT)])
    assert r.exit_code == 0, r.output
    assert "Checked at install, not here: namespace reservations and OIDC" in r.output
