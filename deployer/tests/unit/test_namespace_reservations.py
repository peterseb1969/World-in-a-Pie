"""Namespace reservations: an app's manifest declares the namespaces it owns.

The deployer renders every enabled app's declarations as reservations the
Registry reads (WIP_NAMESPACE_RESERVATIONS_FILE, beside the config-file API
keys), sets each declared env var to its namespace's name, and refuses an
install whose reservations contradict each other.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError as PydanticValidationError

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.config_gen.namespaces import RESERVATIONS_RENDER_PATH
from wip_deploy.discovery import Discovery, discover
from wip_deploy.renderers import render_compose, render_k8s
from wip_deploy.secrets import collect_required_secrets
from wip_deploy.secrets_backend import ResolvedSecrets
from wip_deploy.spec import App, AppRef, Deployment
from wip_deploy.spec.component import Component
from wip_deploy.spec.validators import validate_all

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _app(disc: Discovery, name: str, namespaces: list[dict[str, Any]]) -> App:
    """A real app manifest, renamed, declaring `namespaces` (and without the
    hand-set WIP_NAMESPACE the declaration replaces)."""
    base = next(a for a in disc.apps if a.metadata.name == "wip-cooking")
    raw = base.model_dump()
    raw["metadata"]["name"] = name
    raw["spec"]["env"]["required"] = [
        e for e in raw["spec"]["env"]["required"] if e["name"] != "WIP_NAMESPACE"
    ]
    raw["spec"]["namespaces"] = namespaces
    return App.model_validate(raw)


def _deployment(target: str, apps: list[App]) -> Deployment:
    d = build_deployment(BuildInputs(
        target=target,  # type: ignore[arg-type]
        variant="prod", hostname="wip.example.org", tls="internal",
        registry="ghcr.io/test", compose_data_dir=Path("/tmp/d"), k8s_namespace="wip-t",
    ))
    d.spec.apps = [AppRef(name=a.metadata.name) for a in apps]
    return d


def _secrets(d: Deployment, comps: list[Component], apps: list[App]) -> ResolvedSecrets:
    return ResolvedSecrets(values=dict.fromkeys(collect_required_secrets(d, comps, apps), "x"))


COOKING = [
    {"role": "main", "name": "cooking", "env": "WIP_NAMESPACE",
     "settings": {"isolation_mode": "strict", "deletion_mode": "full",
                  "id_config": {"documents": {"algorithm": "prefixed", "prefix": "RCP-"}}}},
    {"role": "ref", "name": "cooking-ref", "env": "WIP_REF_NAMESPACE"},
]


def test_reservations_rendered_with_declared_settings_only(discovery: Discovery) -> None:
    apps = [_app(discovery, "cooking-app", COOKING)]
    d = _deployment("compose", apps)
    tree = render_compose(d, discovery.components, apps, _secrets(d, discovery.components, apps))
    doc = json.loads(tree.files[Path(RESERVATIONS_RENDER_PATH)].content)
    assert doc == {"reservations": [
        {"prefix": "cooking", "app": "cooking-app", "settings": COOKING[0]["settings"]},
        {"prefix": "cooking-ref", "app": "cooking-app", "settings": {}},
    ]}


def test_rendered_empty_when_no_app_declares_a_namespace(discovery: Discovery) -> None:
    d = _deployment("compose", [])
    tree = render_compose(d, discovery.components, [], _secrets(d, discovery.components, []))
    assert tree.files[Path(RESERVATIONS_RENDER_PATH)].content == ""


def test_declared_env_vars_name_the_namespaces(discovery: Discovery) -> None:
    apps = [_app(discovery, "cooking-app", COOKING)]
    d = _deployment("compose", apps)
    tree = render_compose(d, discovery.components, apps, _secrets(d, discovery.components, apps))
    compose = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
    env = compose["services"]["cooking-app"]["environment"]
    pairs = dict(e.split("=", 1) for e in env) if isinstance(env, list) else env
    assert pairs["WIP_NAMESPACE"] == "cooking"
    assert pairs["WIP_REF_NAMESPACE"] == "cooking-ref"


def test_registry_reads_the_file_from_the_mounted_directory(discovery: Discovery) -> None:
    d = _deployment("compose", [])
    tree = render_compose(d, discovery.components, [], _secrets(d, discovery.components, []))
    registry = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)["services"]["registry"]
    env = registry["environment"]
    pairs = dict(e.split("=", 1) for e in env) if isinstance(env, list) else env
    assert pairs["WIP_NAMESPACE_RESERVATIONS_FILE"] == "/etc/wip/namespace-reservations.json"
    assert "./config/auth:/etc/wip:ro" in registry["volumes"]


def test_k8s_carries_the_file_in_the_auth_config_secret(discovery: Discovery) -> None:
    apps = [_app(discovery, "cooking-app", COOKING)]
    d = _deployment("k8s", apps)
    tree = render_k8s(d, discovery.components, apps, _secrets(d, discovery.components, apps))
    secret = yaml.safe_load(tree.files[Path("api-keys-secret.yaml")].content)
    doc = json.loads(secret["stringData"]["namespace-reservations.json"])
    assert [r["prefix"] for r in doc["reservations"]] == ["cooking", "cooking-ref"]


def test_two_apps_cannot_reserve_one_namespace(discovery: Discovery) -> None:
    apps = [
        _app(discovery, "app-a", [{"role": "main", "name": "shared"}]),
        _app(discovery, "app-b", [{"role": "main", "name": "shared"}]),
    ]
    report = validate_all(_deployment("compose", apps), discovery.components, apps)
    assert any("'shared' is reserved by both" in str(e) for e in report.errors), report.errors


def test_two_reservations_cannot_claim_one_id_prefix(discovery: Discovery) -> None:
    prefixed = {"id_config": {"terms": {"algorithm": "prefixed", "prefix": "X-"}}}
    apps = [
        _app(discovery, "app-a", [{"role": "main", "name": "a", "settings": prefixed}]),
        _app(discovery, "app-b", [{"role": "main", "name": "b", "settings": prefixed}]),
    ]
    report = validate_all(_deployment("compose", apps), discovery.components, apps)
    assert any("'X-' is reserved twice" in str(e) for e in report.errors), report.errors


def test_disabled_apps_reserve_nothing(discovery: Discovery) -> None:
    apps = [
        _app(discovery, "app-a", [{"role": "main", "name": "shared"}]),
        _app(discovery, "app-b", [{"role": "main", "name": "shared"}]),
    ]
    d = _deployment("compose", apps)
    d.spec.apps[1].enabled = False
    assert validate_all(d, discovery.components, apps).ok


@pytest.mark.parametrize("namespaces,message", [
    ([{"role": "ref", "name": "x"}], "role 'main'"),
    ([{"role": "main", "name": "x"}, {"role": "main", "name": "y"}], "duplicate roles"),
    ([{"role": "main", "name": "x"}, {"role": "ref", "name": "x"}], "duplicate names"),
    ([{"role": "main", "name": "wip"}], "belongs to the platform"),
    ([{"role": "main", "name": "x", "settings": {"isolation_mode": "closed"}}], "isolation_mode"),
])
def test_declaration_rules(discovery: Discovery, namespaces, message: str) -> None:
    with pytest.raises(PydanticValidationError, match=message):
        _app(discovery, "bad-app", namespaces)


def test_env_var_set_by_hand_and_by_declaration_is_refused(discovery: Discovery) -> None:
    base = next(a for a in discovery.apps if a.metadata.name == "wip-cooking")
    raw = base.model_dump()
    raw["spec"]["env"]["required"].append(
        {"name": "WIP_NAMESPACE", "source": {"literal": "x"}}
    )
    raw["spec"]["namespaces"] = [{"role": "main", "name": "x", "env": "WIP_NAMESPACE"}]
    with pytest.raises(PydanticValidationError, match="WIP_NAMESPACE is set from the namespace"):
        App.model_validate(raw)


def test_components_cannot_declare_namespaces(discovery: Discovery) -> None:
    raw = discovery.components[0].model_dump()
    raw["spec"]["namespaces"] = [{"role": "main", "name": "x"}]
    with pytest.raises(PydanticValidationError, match="declared by apps"):
        Component.model_validate(raw)
