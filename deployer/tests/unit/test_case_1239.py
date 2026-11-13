"""An app reads only the secrets it owns.

A manifest is written by an app's author, not the operator, and every secret it
names through `from_secret` reaches its container's environment. Only `api-key`
was refused, so `from_secret: app-dnd-api-key` handed one app another's key
(admin on that app's namespaces), and `mongo-root-password` or a component's
secret worked the same way.

Two rules now hold:
- the manifest (App.reads_only_its_own_secrets): a deployer-supplied secret is
  the app's own NATS or MongoDB user password, or the master key for a
  privileged_key app; anything operator-supplied is left to the next rule;
- the install (validators._validate_app_operator_secrets): an operator-supplied
  name is refused when another owner uses it, so marking `app-dnd-api-key`
  `supplied_by: operator` does not get around the first rule. Operator secrets
  shared between apps, such as `anthropic-api-key`, stay allowed.
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
from wip_deploy.spec.validators import _validate_app_operator_secrets

REPO_ROOT = Path(__file__).resolve().parents[3]
COOKING = yaml.safe_load((REPO_ROOT / "apps/wip-cooking/wip-app.yaml").read_text())


@pytest.fixture(scope="module")
def discovery():
    return discover(REPO_ROOT)


def _cooking(*, required: list[dict] | None = None, optional: list[dict] | None = None) -> dict:
    raw = copy.deepcopy(COOKING)
    raw["spec"]["env"]["required"] = [*raw["spec"]["env"].get("required", []), *(required or [])]
    raw["spec"]["env"]["optional"] = [*raw["spec"]["env"].get("optional", []), *(optional or [])]
    return raw


def _secret(name: str, secret: str, *, operator: bool = False) -> dict:
    source: dict = {"from_secret": secret}
    if operator:
        source["supplied_by"] = "operator"
    return {"name": name, "source": source}


# ── the manifest rule ────────────────────────────────────────────────────


@pytest.mark.parametrize("secret", ["app-dnd-api-key", "mongo-root-password", "garage-key-secret", "jira-token"])
def test_a_deployer_supplied_secret_the_app_does_not_own_is_refused(secret: str) -> None:
    with pytest.raises(ValidationError, match=f"OTHER \\({secret}\\) reads a secret the app does not own"):
        App.model_validate(_cooking(required=[_secret("OTHER", secret)]))


def test_an_optional_deployer_supplied_foreign_secret_is_refused_too() -> None:
    with pytest.raises(ValidationError, match="does not own"):
        App.model_validate(_cooking(optional=[_secret("OTHER", "app-dnd-api-key")]))


def test_an_operator_supplied_secret_passes_the_manifest(discovery) -> None:
    App.model_validate(_cooking(optional=[_secret("JIRA", "jira-token", operator=True)]))


@pytest.mark.parametrize("operator", [False, True])
def test_the_master_key_still_gets_its_own_message(operator: bool) -> None:
    # Refused whoever claims to supply it, so it never reaches the install rule.
    raw = _cooking(optional=[_secret("KEY", "api-key", operator=operator)])
    with pytest.raises(ValidationError, match="reads the install's master key"):
        App.model_validate(raw)


def test_every_shipped_app_passes(discovery) -> None:
    assert discovery.errors == []
    names = {a.metadata.name for a in discovery.apps}
    assert {"brmri", "react-console", "wip-kb"} <= names


def test_brmri_takes_its_outside_credentials_from_the_operator(discovery) -> None:
    brmri = next(a for a in discovery.apps if a.metadata.name == "brmri")
    assert {(e.name, e.source.from_secret, e.source.supplied_by) for e in brmri.spec.env.optional} == {
        ("JIRA_TOKEN", "jira-token", "operator"),
        ("ANTHROPIC_API_KEY", "anthropic-api-key", "operator"),
    }
    assert not any(e.source.from_secret for e in brmri.spec.env.required)


# ── the install rule ─────────────────────────────────────────────────────


def _install(discovery, app_raw: dict, *, enabled: bool = True) -> list[str]:
    app = App.model_validate(app_raw)
    apps = [a for a in discovery.apps if a.metadata.name != app.metadata.name] + [app]
    names = ["dnd", app.metadata.name] if enabled else ["dnd"]
    deployment = build_deployment(BuildInputs(
        name="t", preset="standard", target="compose", hostname="wip.example.org",
        compose_data_dir=Path("/tmp/wip-1239"), apps=names, add=["garage"],
    ))
    return [str(e) for e in _validate_app_operator_secrets(deployment, discovery.components, apps)]


@pytest.mark.parametrize("secret,owner", [
    ("app-dnd-api-key", "app 'dnd''s own key"),
    ("mongo-root-password", "a secret the deployer generates"),
    ("nats-password-registry", "a secret the deployer generates"),
    ("dex-password-admin.bcrypt", "a secret the deployer generates"),
    ("dex-client-wip-gateway", "component 'auth-gateway'"),
    ("garage-key-secret", "a secret the deployer generates"),
    ("gateway-session-secret", "component 'auth-gateway'"),
])
def test_an_operator_supplied_name_another_owner_uses_is_refused(discovery, secret: str, owner: str) -> None:
    errs = _install(discovery, _cooking(optional=[_secret("STOLEN", secret, operator=True)]))
    assert len(errs) == 1
    assert f"names {secret!r} as an operator-supplied secret, but that is {owner}" in errs[0]


def test_a_shared_operator_secret_stays_allowed(discovery) -> None:
    # wip-cooking already reads anthropic-api-key from the operator, as dnd does.
    assert _install(discovery, _cooking()) == []


def test_an_app_that_is_not_enabled_is_not_checked(discovery) -> None:
    raw = _cooking(optional=[_secret("STOLEN", "app-dnd-api-key", operator=True)])
    assert _install(discovery, raw, enabled=False) == []
