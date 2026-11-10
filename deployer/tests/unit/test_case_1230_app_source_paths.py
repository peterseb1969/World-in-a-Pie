"""CASE-1230 item 3 — app source paths must mean what the operator typed.

Two substitutions were happening behind an honest-looking flag:

  - `add-app --app-source <path>` stored the value verbatim, where `install`
    resolves it. A relative path then rendered as `build.context: <relative>`,
    which compose resolves against the *compose file's* directory — the install
    dir under `~/.wip-deploy/`, not the cwd the path was typed in.
  - A path registered on the machine was back-filled into `app_sources` for any
    enabled app, with no exemption for names listed in `apps_from_registry`.
    Since `dev_simple` tests `name in app_sources` before the registry branch,
    the explicit `--app-from-registry` opt-in became unreachable and the local
    checkout silently won.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from wip_deploy.cli import app

runner = CliRunner()
WIP_ROOT = Path(__file__).resolve().parents[3]

_STATE = {
    "metadata": {"name": "test-install"},
    "spec": {
        "target": "dev",
        "apps": [],
        "modules": {"optional": []},
        "auth": {"mode": "api-key-only", "gateway": False, "users": []},
        "network": {"hostname": "wip.localhost"},
        "images": {"tag_overrides": {}},
        "platform": {"compose": {"data_dir": "/tmp/wip-test-data"}, "dev": {}},
        "secrets": {"backend": "file", "location": "/tmp/wip-test-secrets"},
        "apply": {},
    },
}


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    return fake_home


def _dev_install(tmp_path: Path) -> Path:
    install = tmp_path / "install"
    install.mkdir(parents=True)
    (install / "deployment.deployer-state").write_text(
        json.dumps({"wip_deploy_format_version": 1, "deployment": _STATE}, indent=2)
    )
    return install


def _add_app(install: Path, app_name: str, source_arg: str):
    with patch("wip_deploy.cli._apply_and_persist_mutation") as mutate:
        result = runner.invoke(
            app,
            [
                "add-app", app_name,
                "--app-source", source_arg,
                "--name", "test-install",
                "--install-dir", str(install),
            ],
        )
    return result, mutate


def test_add_app_stores_an_absolute_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A relative path is resolved where it was typed, not left for compose."""
    monkeypatch.chdir(WIP_ROOT)
    install = _dev_install(tmp_path)

    result, mutate = _add_app(install, "wip-kb", "apps/dev-kb")
    assert result.exit_code == 0, result.output
    deployment = mutate.call_args.args[0]
    stored = deployment.spec.platform.dev.app_sources["wip-kb"]
    assert stored == WIP_ROOT / "apps" / "dev-kb"
    assert stored.is_absolute()


def test_add_app_expands_a_quoted_tilde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> None:
    """`~/…` works here as it does for `install` and check-app-deployability.

    Pre-fix typer left the tilde literal, so the is_dir() check rejected a path
    that exists — an error naming a directory the operator could cd into.
    """
    checkout = isolated_home / "kb-checkout"
    checkout.mkdir(parents=True)
    monkeypatch.chdir(WIP_ROOT)
    install = _dev_install(tmp_path)

    result, mutate = _add_app(install, "wip-kb", "~/kb-checkout")
    assert result.exit_code == 0, result.output
    stored = mutate.call_args.args[0].spec.platform.dev.app_sources["wip-kb"]
    assert stored == checkout.resolve()
    assert "~" not in str(stored)


def test_a_nonexistent_path_still_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Normalization must not swallow a genuinely missing checkout."""
    monkeypatch.chdir(WIP_ROOT)
    install = _dev_install(tmp_path)

    result, mutate = _add_app(install, "wip-kb", "apps/nope-not-here")
    assert result.exit_code == 2, result.output
    assert "not a directory" in result.output
    mutate.assert_not_called()


def test_a_registered_path_does_not_override_the_registry_opt_in(
    isolated_home: Path, tmp_path: Path
) -> None:
    """The deliberate flag wins over machine state, and says when it did."""
    src = tmp_path / "kb-checkout"
    src.mkdir()
    assert runner.invoke(app, ["register-app", "wip-kb", "--path", str(src)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "show-spec",
            "--target", "dev",
            "--hostname", "wip.localhost",
            "--app", "wip-kb",
            "--app-from-registry", "wip-kb",
            "--name", "test-1230",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "--app-from-registry 'wip-kb'" in result.output
    # CliRunner merges stderr into `output`, so the CASE-366 stderr warning
    # prefixes the payload here even though a real `| jq` pipe stays clean.
    payload = json.loads(result.output[result.output.index("{"):])
    assert "wip-kb" not in payload["spec"]["platform"]["dev"]["app_sources"]
    assert "wip-kb" in payload["spec"]["platform"]["dev"]["apps_from_registry"]


def test_the_registered_path_still_backfills_without_the_opt_in(
    isolated_home: Path, tmp_path: Path
) -> None:
    """The CASE-356 convenience must survive: this is a guard, not a repro."""
    src = tmp_path / "kb-checkout"
    src.mkdir()
    assert runner.invoke(app, ["register-app", "wip-kb", "--path", str(src)]).exit_code == 0

    result = runner.invoke(
        app,
        [
            "show-spec",
            "--target", "dev",
            "--hostname", "wip.localhost",
            "--app", "wip-kb",
            "--name", "test-1230",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    spec = json.loads(result.output)
    assert spec["spec"]["platform"]["dev"]["app_sources"]["wip-kb"] == str(src.resolve())
