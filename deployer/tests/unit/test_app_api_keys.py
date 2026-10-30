"""Every app runs with a key of its own, never the install's master key.

The deployer derives one config-file key per enabled app from its manifest:
admin on the namespaces it owns (spec.namespaces), read or write on those it
lists in spec.namespace_access, read on `wip`. It sets the app's WIP_API_KEY
from that key's secret. Only an app declaring privileged_key (an admin
console) may hold the master key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError as PydanticValidationError

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.config_gen.api_keys import API_KEYS_RENDER_PATH, app_api_key
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_k8s
from wip_deploy.secrets import collect_required_secrets
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import App, AppRef, Deployment
from wip_deploy.spec.deployment import SpecAPIKey
from wip_deploy.spec.validators import validate_all

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _app(disc: Discovery, name: str, **spec: Any) -> App:
    base = next(a for a in disc.apps if a.metadata.name == "wip-cooking")
    raw = base.model_dump()
    raw["metadata"]["name"] = name
    raw["spec"]["namespaces"] = []
    raw["spec"].update(spec)
    return App.model_validate(raw)


def _deployment(target: str, apps: list[App]) -> Deployment:
    d = build_deployment(BuildInputs(
        target=target,  # type: ignore[arg-type]
        variant="prod", hostname="wip.example.org", tls="internal",
        registry="ghcr.io/test", compose_data_dir=Path("/tmp/d"), k8s_namespace="wip-t",
    ))
    d.spec.apps = [AppRef(name=a.metadata.name) for a in apps]
    return d


def _secrets(d: Deployment, comps: list, apps: list[App]) -> ResolvedSecrets:
    return ResolvedSecrets(values={
        n: f"secret-of-{n}" for n in collect_required_secrets(d, comps, apps)
    })


OWNER = {
    "namespaces": [
        {"role": "main", "name": "cooking", "env": "WIP_NAMESPACE"},
        {"role": "ref", "name": "cooking-ref", "env": "WIP_REF_NAMESPACE"},
    ],
    "namespace_access": [{"namespace": "library", "permission": "write"},
                         {"namespace": "kb"}],
}


def test_derived_key_scope(discovery: Discovery) -> None:
    key = app_api_key(_app(discovery, "cooking-app", **OWNER))
    assert key is not None
    assert key.name == "app-cooking-app"
    assert key.secret_name == "app-cooking-app-api-key"
    assert key.namespaces == ["cooking", "cooking-ref", "library", "kb", "wip"]
    assert key.grants == {"cooking": "admin", "cooking-ref": "admin", "library": "write"}
    assert key.groups == []


def test_an_app_owning_nothing_still_reads_wip(discovery: Discovery) -> None:
    key = app_api_key(_app(discovery, "reader"))
    assert key is not None and key.namespaces == ["wip"] and key.grants == {}


def test_rendered_key_file_and_app_env(discovery: Discovery) -> None:
    apps = [_app(discovery, "cooking-app", **OWNER)]
    d = _deployment("compose", apps)
    tree = render_compose(d, discovery.components, apps, _secrets(d, discovery.components, apps))
    keys = {k["name"]: k for k in json.loads(tree.files[Path(API_KEYS_RENDER_PATH)].content)["keys"]}
    entry = keys["app-cooking-app"]
    assert entry["key"] == "secret-of-app-cooking-app-api-key"
    assert entry["grants"] == {"cooking": "admin", "cooking-ref": "admin", "library": "write"}
    assert entry["groups"] == []

    compose = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
    env = compose["services"]["cooking-app"]["environment"]
    pairs = dict(e.split("=", 1) for e in env) if isinstance(env, list) else env
    # Compose passes secrets by reference to the install's .env.
    assert pairs["WIP_API_KEY"] == "${APP_COOKING_APP_API_KEY}"
    dotenv = tree.files[Path(".env")].content
    assert "APP_COOKING_APP_API_KEY=secret-of-app-cooking-app-api-key" in dotenv
    assert "secret-of-api-key" not in str(pairs)


def test_k8s_reads_the_app_key_from_its_secret(discovery: Discovery) -> None:
    apps = [_app(discovery, "cooking-app", **OWNER)]
    d = _deployment("k8s", apps)
    tree = render_k8s(d, discovery.components, apps, _secrets(d, discovery.components, apps))
    workload = next(
        doc for doc in yaml.safe_load_all(tree.files[Path("services/cooking-app.yaml")].content)
        if doc and doc["kind"] == "Deployment"
    )
    env = {e["name"]: e for e in workload["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["WIP_API_KEY"]["valueFrom"]["secretKeyRef"]["key"] == "app-cooking-app-api-key"


def test_disabled_apps_get_no_key(discovery: Discovery) -> None:
    apps = [_app(discovery, "cooking-app", **OWNER)]
    d = _deployment("compose", apps)
    d.spec.apps[0].enabled = False
    assert "app-cooking-app-api-key" not in collect_required_secrets(d, discovery.components, apps)


def test_only_a_privileged_app_may_mount_the_master_key(discovery: Discovery) -> None:
    master = {"env": {"required": [
        {"name": "WIP_API_KEY", "source": {"from_secret": "api-key"}},
    ]}}
    with pytest.raises(PydanticValidationError, match="master key"):
        _app(discovery, "sneaky", **master)
    console = _app(discovery, "console", privileged_key=True, **master)
    assert app_api_key(console) is None


def test_the_deployer_owns_wip_api_key(discovery: Discovery) -> None:
    with pytest.raises(PydanticValidationError, match="WIP_API_KEY is set by the deployer"):
        _app(discovery, "x", env={"required": [
            {"name": "WIP_API_KEY", "source": {"literal": "hand-made"}},
        ]})


@pytest.mark.parametrize("spec,message", [
    ({"privileged_key": True, "namespaces": [{"role": "main", "name": "x"}]}, "never gets"),
    ({"namespaces": [{"role": "main", "name": "x"}],
      "namespace_access": [{"namespace": "x"}]}, "both owned"),
    ({"namespace_access": [{"namespace": "x"}, {"namespace": "x", "permission": "write"}]},
     "twice"),
    ({"namespace_access": [{"namespace": "x", "permission": "admin"}]}, "permission"),
])
def test_declaration_rules(discovery: Discovery, spec: dict, message: str) -> None:
    with pytest.raises(PydanticValidationError, match=message):
        _app(discovery, "bad", **spec)


def test_a_declared_key_cannot_take_an_app_keys_name(discovery: Discovery) -> None:
    apps = [_app(discovery, "cooking-app", **OWNER)]
    d = _deployment("compose", apps)
    d.spec.auth.api_keys = [SpecAPIKey(name="app-cooking-app", namespaces=["x"])]
    report = validate_all(d, discovery.components, apps)
    assert any("'app-cooking-app'" in str(e) for e in report.errors), report.errors


def test_every_shipped_app_but_the_console_runs_with_its_own_key(discovery: Discovery) -> None:
    privileged = sorted(a.metadata.name for a in discovery.apps if a.spec.privileged_key)
    assert privileged == ["react-console"]
    for app in discovery.apps:
        mounts_master = any(
            e.source.from_secret == "api-key"
            for e in (*app.spec.env.required, *app.spec.env.optional)
        )
        assert mounts_master == app.spec.privileged_key, app.metadata.name
