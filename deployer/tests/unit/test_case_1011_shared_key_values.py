"""CASE-1011 — one value per key, and an app key is changed through the deployer.

A runtime key's plaintext pasted into an app's key secret made two keys share
one value. Services match a presented key against their keys in order and
cache the first match, so they authenticated it as different keys and
disagreed about what the caller may reach.

Contract under test:
  - an apply refuses key secrets (app keys, spec-declared keys, the canary,
    the master key) that share a value, naming them, and writes nothing to
    the store when it refuses;
  - a refused apply because an app key changed says what changes an app's
    reach (`app-namespace add`) and how to give it a fresh key
    (`rotate-key app-<app>`);
  - `rotate-key app-<app>` gives an app's key a fresh value.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _ensure_secrets_via_spec, _persist_deployment, app
from wip_deploy.config_gen.api_keys import app_key_secrets, shared_api_key_values
from wip_deploy.discovery import Discovery, discover
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import AppRef, Deployment

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(tmp_path: Path) -> Deployment:
    d = build_deployment(
        BuildInputs(
            target="compose",
            variant="dev",
            hostname="wip.localhost",
            tls="internal",
            compose_data_dir=tmp_path / "data",
            k8s_namespace="wip-test",
            registry="ghcr.io/peterseb1969",
        )
    )
    d.spec.apps = [AppRef(name="wip-kb"), AppRef(name="wip-cooking")]
    secrets = d.spec.secrets.model_copy(update={"location": str(tmp_path / "secrets")})
    return d.model_copy(update={"spec": d.spec.model_copy(update={"secrets": secrets})})


def _applied(tmp_path: Path, discovery: Discovery) -> Deployment:
    d = _deployment(tmp_path)
    s = _ensure_secrets_via_spec(d, discovery.components, discovery.apps, install_dir=tmp_path)
    _persist_deployment(d, tmp_path, repo_root=REPO_ROOT, secrets=s)
    return d


class TestSharedValues:
    def test_distinct_values_share_nothing(self, tmp_path: Path, discovery: Discovery) -> None:
        d = _deployment(tmp_path)
        names = ["api-key", "app-wip-kb-api-key", "app-wip-cooking-api-key", "wip-canary-api-key"]
        secrets = ResolvedSecrets(values={n: f"value-of-{n}" for n in names})
        assert shared_api_key_values(d, discovery.apps, secrets) == []

    def test_an_app_key_equal_to_another_or_the_master_key_is_found(
        self, tmp_path: Path, discovery: Discovery,
    ) -> None:
        d = _deployment(tmp_path)
        secrets = ResolvedSecrets(values={
            "api-key": "master",
            "app-wip-kb-api-key": "master",
            "app-wip-cooking-api-key": "pasted",
            "wip-canary-api-key": "pasted",
        })
        assert shared_api_key_values(d, discovery.apps, secrets) == [
            ["api-key", "app-wip-kb-api-key"],
            ["app-wip-cooking-api-key", "wip-canary-api-key"],
        ]

    def test_app_key_secrets_maps_each_app(self, tmp_path: Path, discovery: Discovery) -> None:
        assert app_key_secrets(_deployment(tmp_path), discovery.apps) == {
            "app-wip-kb-api-key": "wip-kb",
            "app-wip-cooking-api-key": "wip-cooking",
        }

    def test_the_apply_refuses_and_writes_nothing(
        self, tmp_path: Path, discovery: Discovery, capsys: pytest.CaptureFixture[str],
    ) -> None:
        d = _deployment(tmp_path)
        store = tmp_path / "secrets"
        store.mkdir()
        (store / "api-key").write_text("one-value")
        (store / "app-wip-kb-api-key").write_text("one-value")

        with pytest.raises(typer.Exit) as e:
            _ensure_secrets_via_spec(d, discovery.components, discovery.apps)
        assert e.value.exit_code == 2
        err = capsys.readouterr().err
        assert "api-key, app-wip-kb-api-key" in err
        assert "rotate-key" in err
        # Nothing generated was written: the store holds only what it held.
        assert sorted(p.name for p in store.iterdir()) == ["api-key", "app-wip-kb-api-key"]


def test_a_changed_app_key_names_what_changes_an_apps_reach(
    tmp_path: Path, discovery: Discovery, capsys: pytest.CaptureFixture[str],
) -> None:
    d = _applied(tmp_path, discovery)
    (tmp_path / "secrets" / "app-wip-kb-api-key").write_text("pasted-from-the-console")
    capsys.readouterr()

    with pytest.raises(typer.Exit):
        _ensure_secrets_via_spec(d, discovery.components, discovery.apps, install_dir=tmp_path)
    err = capsys.readouterr().err
    assert "wip-deploy app-namespace add wip-kb <namespace>" in err
    assert "wip-deploy rotate-key app-wip-kb" in err
    # Only the changed app key gets the hint.
    assert "app-namespace add wip-cooking" not in err


def test_rotate_key_gives_an_app_key_a_fresh_value(tmp_path: Path, discovery: Discovery) -> None:
    _applied(tmp_path, discovery)
    key_file = tmp_path / "secrets" / "app-wip-kb-api-key"
    old = key_file.read_text()
    with patch("wip_deploy.cli.apply_compose") as apply:
        apply.return_value = MagicMock(healthy=True, post_install_skipped=[])
        result = runner.invoke(
            app,
            ["rotate-key", "app-wip-kb", "--no-check",
             "--install-dir", str(tmp_path), "--repo-root", str(REPO_ROOT)],
        )
    assert result.exit_code == 0, result.output
    assert key_file.read_text() != old
