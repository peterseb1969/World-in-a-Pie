"""Who may read which secret: the master key is the operator's to grant, and
neither an app nor a component reads another owner's secret.

- `privileged_key: true` is a line in a manifest the app's author writes; the
  master key it unlocks is admin on every namespace. The install grants it per
  app (`--privileged-app`, `add-app --privileged`); an app asking without the
  grant is refused, and so is a grant to an app that does not ask.
- A component reads the platform's shared secrets but never another owner's:
  an app's key, another service's NATS/MongoDB password, the canary's or the
  app edge's key, MongoDB's root password.
- An app may read its own derived key under a name of its own.
- A component's operator-supplied secret is shared, like an app's.
- `validate-manifest` refuses an operator-supplied name another owner in the
  repo already uses, before an install does.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.discovery import discover
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component
from wip_deploy.spec.validators import _validate_app_operator_secrets, _validate_privileged_apps

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def discovery():
    return discover(REPO_ROOT)


COOKING = yaml.safe_load((REPO_ROOT / "apps/wip-cooking/wip-app.yaml").read_text())


def _app(name: str = "foo", *, privileged: bool = False, env: dict | None = None) -> App:
    raw = copy.deepcopy(COOKING)
    raw["metadata"]["name"] = name
    raw["app_metadata"]["label"] = name
    raw["spec"]["env"] = env or {}
    if privileged:
        raw["spec"]["privileged_key"] = True
        raw["spec"].pop("namespaces", None)
        raw["spec"].pop("namespace_access", None)
    return App.model_validate(raw)


def _install(apps: list[str], privileged: list[str] | None = None):
    return build_deployment(BuildInputs(
        name="t", preset="standard", target="compose", hostname="wip.example.org",
        compose_data_dir=Path("/tmp/wip-1230"), apps=apps,
        privileged_apps=privileged or [],
    ))


# ── the master key needs the operator's grant ───────────────────────────────


def test_an_app_asking_for_the_master_key_without_the_grant_is_refused() -> None:
    [error] = _validate_privileged_apps(_install(["foo"]), [_app(privileged=True)])
    assert "privileged_key" in str(error) and "--privileged" in str(error)


def test_the_grant_lets_it_hold_the_master_key() -> None:
    assert _validate_privileged_apps(_install(["foo"], ["foo"]), [_app(privileged=True)]) == []


def test_a_grant_to_an_app_that_does_not_ask_is_refused() -> None:
    [error] = _validate_privileged_apps(_install(["foo"], ["foo"]), [_app()])
    assert "does not declare privileged_key" in str(error)


def test_the_grant_needs_the_app_enabled() -> None:
    with pytest.raises(ValueError, match="does not enable"):
        _install(["bar"], ["foo"])


def test_react_console_is_refused_until_granted(discovery) -> None:
    from wip_deploy.spec.validators import validate_all

    plain = validate_all(_install(["react-console"]), discovery.components, discovery.apps)
    assert any("react-console" in str(e) and "privileged_key" in str(e) for e in plain.errors)
    granted = validate_all(
        _install(["react-console"], ["react-console"]), discovery.components, discovery.apps,
    )
    assert not any("privileged_key" in str(e) for e in granted.errors)


# ── an app may read its own key ─────────────────────────────────────────────


def test_an_app_may_read_its_own_key_under_another_name() -> None:
    _app(env={"required": [{"name": "MY_TOKEN", "source": {"from_secret": "app-foo-api-key"}}]})


def test_an_app_may_not_read_another_apps_key() -> None:
    with pytest.raises(ValidationError, match="does not own"):
        _app(env={"required": [{"name": "T", "source": {"from_secret": "app-bar-api-key"}}]})


# ── components read no other owner's secret ─────────────────────────────────


def _component(name: str, secret: str, **source) -> Component:
    section = "optional" if source.get("supplied_by") == "operator" else "required"
    return Component.model_validate({
        "metadata": {"name": name, "category": "optional", "description": "test"},
        "spec": {
            "image": {"name": f"ghcr.io/x/{name}"},
            "env": {section: [{"name": "S", "source": {"from_secret": secret, **source}}]},
        },
    })


@pytest.mark.parametrize("secret", [
    "app-dnd-api-key", "nats-password-registry", "mongo-password-registry",
    "wip-canary-api-key", "wip-app-edge-api-key", "mongo-root-password",
])
def test_a_component_may_not_read_another_owners_secret(secret: str) -> None:
    with pytest.raises(ValidationError, match="belongs to another owner"):
        _component("foo", secret)


@pytest.mark.parametrize(("name", "secret"), [
    ("foo", "api-key"), ("foo", "postgres-password"), ("foo", "garage-key-id"),
    ("foo", "nats-password-foo"), ("foo", "mongo-password-foo"),
    ("app-edge", "wip-app-edge-api-key"), ("mongodb", "mongo-root-password"),
])
def test_a_component_reads_shared_and_its_own_secrets(name: str, secret: str) -> None:
    _component(name, secret)


def test_an_operator_supplied_name_is_the_operators() -> None:
    _component("foo", "app-dnd-api-key", supplied_by="operator")


def test_every_shipped_component_passes(discovery) -> None:
    assert discovery.ok, discovery.errors


def test_a_components_operator_secret_is_shared_not_owned(discovery) -> None:
    shared = Component.model_validate({
        "metadata": {"name": "llm", "category": "optional", "description": "test"},
        "spec": {
            "image": {"name": "ghcr.io/x/llm"},
            "env": {"optional": [{"name": "K", "source": {
                "from_secret": "anthropic-api-key", "supplied_by": "operator"}}]},
        },
    })
    app = _app(env={"optional": [{"name": "K", "source": {
        "from_secret": "anthropic-api-key", "supplied_by": "operator"}}]})
    errors = _validate_app_operator_secrets(_install(["foo"]), [*discovery.components, shared], [app])
    assert errors == []


# ── validate-manifest refuses an operator name another owner uses ───────────


@pytest.mark.parametrize("secret", [
    # api-key is the App model's to refuse (any supply), before this check.
    "app-dnd-api-key", "nats-password-anything", "wip-canary-api-key",
    "postgres-password", "mongo-root-password",
])
def test_validate_manifest_refuses_a_foreign_operator_name(tmp_path: Path, secret: str) -> None:
    from wip_deploy.validate_manifest import validate_manifest

    manifest = _app("newapp").model_dump(mode="json", exclude_none=True)
    manifest["spec"]["env"] = {"optional": [{"name": "K", "source": {
        "from_secret": secret, "supplied_by": "operator"}}]}
    path = tmp_path / "wip-app.yaml"
    path.write_text(yaml.safe_dump(manifest))

    app, errors = validate_manifest(path, REPO_ROOT)
    assert app is not None
    assert any(secret in e.message and "install refuses it" in e.message for e in errors), errors


def test_validate_manifest_accepts_a_name_of_the_apps_own(tmp_path: Path) -> None:
    from wip_deploy.validate_manifest import validate_manifest

    manifest = _app("newapp").model_dump(mode="json", exclude_none=True)
    manifest["spec"]["env"] = {"optional": [{"name": "K", "source": {
        "from_secret": "newapp-llm-key", "supplied_by": "operator"}}]}
    path = tmp_path / "wip-app.yaml"
    path.write_text(yaml.safe_dump(manifest))

    _app_obj, errors = validate_manifest(path, REPO_ROOT)
    assert errors == []
