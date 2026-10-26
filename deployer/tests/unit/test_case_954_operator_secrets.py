"""CASE-954 — an operator-supplied optional secret reaches its app.

Optional `from_secret` vars were filled only when the secret was collected,
and collection reads required vars only, so `ANTHROPIC_API_KEY` never reached
an app even with secrets/anthropic-api-key in the store — silently. The
deployer-generated optional secrets (the Garage keys, optional because Garage
may be inactive) must keep that rule: their files outlive `remove-module`.
`supplied_by: operator` tells the two apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
import yaml
from pydantic import ValidationError

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import _ensure_secrets_via_spec, _persist_deployment
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose
from wip_deploy.secrets import ensure_secrets, secret_digest
from wip_deploy.secrets_backend import FileSecretBackend
from wip_deploy.spec import Deployment
from wip_deploy.spec.component import EnvSource, EnvSpec, EnvVar
from wip_deploy.spec.deployment import AppRef

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _deployment(tmp_path: Path, *, garage: bool = True) -> Deployment:
    d = build_deployment(BuildInputs(
        target="compose", variant="dev", hostname="localhost", tls="internal",
        registry="ghcr.io/test", compose_data_dir=tmp_path / "data",
    ))
    d.spec.apps = [AppRef(name="react-console")]
    if not garage:
        d.spec.modules.optional = [m for m in d.spec.modules.optional if m != "garage"]
    d.spec.secrets.location = str(tmp_path / "secrets")
    return d


def _store(tmp_path: Path) -> FileSecretBackend:
    return FileSecretBackend(tmp_path / "secrets")


class TestSpec:
    def test_operator_needs_from_secret(self) -> None:
        with pytest.raises(ValidationError, match="from_secret only"):
            EnvSource(literal="x", supplied_by="operator")

    def test_operator_secret_cannot_be_required(self) -> None:
        var = EnvVar(name="K", source=EnvSource(from_secret="k", supplied_by="operator"))
        with pytest.raises(ValidationError, match="only feed an optional"):
            EnvSpec(required=[var])


class TestEnsureSecrets:
    def test_present_is_read_and_never_counted_as_generated(
        self, tmp_path: Path, discovery: Discovery,
    ) -> None:
        d = _deployment(tmp_path)
        (tmp_path / "secrets").mkdir()
        (tmp_path / "secrets" / "anthropic-api-key").write_text("sk-test")
        s = ensure_secrets(d, discovery.components, discovery.apps, _store(tmp_path))
        assert s.values["anthropic-api-key"] == "sk-test"
        assert "anthropic-api-key" not in s.generated

    def test_absent_is_never_generated(self, tmp_path: Path, discovery: Discovery) -> None:
        d = _deployment(tmp_path)
        s = ensure_secrets(d, discovery.components, discovery.apps, _store(tmp_path))
        assert "anthropic-api-key" not in s.values
        assert not (tmp_path / "secrets" / "anthropic-api-key").exists()

    def test_a_leftover_garage_key_stays_out(self, tmp_path: Path, discovery: Discovery) -> None:
        """Deployer-generated optional secrets keep the source-gated rule."""
        ensure_secrets(_deployment(tmp_path), discovery.components, discovery.apps, _store(tmp_path))
        assert (tmp_path / "secrets" / "garage-key-secret").exists()
        s = ensure_secrets(
            _deployment(tmp_path, garage=False), discovery.components, discovery.apps, _store(tmp_path),
        )
        assert "garage-key-secret" not in s.values


def _env(tmp_path: Path, discovery: Discovery) -> dict[str, str]:
    d = _deployment(tmp_path)
    s = ensure_secrets(d, discovery.components, discovery.apps, _store(tmp_path))
    tree = render_compose(d, discovery.components, discovery.apps, s)
    services = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)["services"]
    return services["react-console"].get("environment") or {}


def test_the_var_reaches_the_app_only_when_the_secret_exists(
    tmp_path: Path, discovery: Discovery,
) -> None:
    assert "ANTHROPIC_API_KEY" not in _env(tmp_path, discovery)
    (tmp_path / "secrets" / "anthropic-api-key").write_text("sk-test")
    assert "ANTHROPIC_API_KEY" in _env(tmp_path, discovery)


def test_a_skip_is_reported(
    tmp_path: Path, discovery: Discovery, capsys: pytest.CaptureFixture[str],
) -> None:
    _ensure_secrets_via_spec(_deployment(tmp_path), discovery.components, discovery.apps)
    err = capsys.readouterr().err
    assert "react-console: ANTHROPIC_API_KEY not set" in err
    assert str(tmp_path / "secrets" / "anthropic-api-key") in err
    assert "garage" not in err


def test_changing_or_removing_the_key_is_not_drift(tmp_path: Path, discovery: Discovery) -> None:
    """The operator rotates and removes it on purpose; CASE-951's check must not refuse."""
    d = _deployment(tmp_path)
    (tmp_path / "secrets").mkdir()
    key = tmp_path / "secrets" / "anthropic-api-key"
    key.write_text("sk-one")
    s = _ensure_secrets_via_spec(d, discovery.components, discovery.apps, install_dir=tmp_path)
    _persist_deployment(d, tmp_path, repo_root=REPO_ROOT, secrets=s)

    key.write_text("sk-two")
    s = _ensure_secrets_via_spec(d, discovery.components, discovery.apps, install_dir=tmp_path)
    assert secret_digest(s.values["anthropic-api-key"]) == secret_digest("sk-two")

    key.unlink()
    try:
        s = _ensure_secrets_via_spec(d, discovery.components, discovery.apps, install_dir=tmp_path)
    except typer.Exit as e:  # pragma: no cover - a refusal is the bug
        pytest.fail(f"removing an operator secret was refused (exit {e.exit_code})")
    assert "anthropic-api-key" not in s.values
