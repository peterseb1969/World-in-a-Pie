"""The app edge is an optional module (CASE-1068).

`app-edge` is enabled through modules.optional like any optional module: the
gateway presets list it, `add-module` / `remove-module` toggle it, and a spec
validator refuses it without the auth gateway or with core suppressed. One
source decides whether it runs, so the component, its key, its k8s egress
policy and the attach policy's `app_edge` field agree. A saved spec from
before the module existed (deployer-state format 1) is upgraded once on load:
an install that ran the edge keeps it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment, preset_edge_dropped
from wip_deploy.cli import (
    _load_deployment,
    _persist_deployment,
    _state_v1_to_v2,
    _try_load_previous_deployment,
    _upgrade_state_payload,
    app,
)
from wip_deploy.config_gen.api_keys import APP_EDGE_KEY, app_edge_active, effective_api_keys
from wip_deploy.config_gen.attach_policy import attach_policy_json
from wip_deploy.discovery import Discovery, discover
from wip_deploy.presets import PRESETS
from wip_deploy.renderers import render_compose, render_k8s
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.deployment import APP_EDGE_MODULE
from wip_deploy.spec.validators import validate_all

from .renderers.test_compose import REPO_ROOT, _minimal_compose
from .renderers.test_compose import _secrets as _compose_secrets
from .renderers.test_k8s import _k8s_deployment
from .renderers.test_k8s import _secrets as _k8s_secrets

runner = CliRunner()
EDGE_MODULES = ["mcp-server", APP_EDGE_MODULE]


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _edge(discovery: Discovery):
    return next(c for c in discovery.components if c.metadata.name == APP_EDGE_MODULE)


def _errors(d, discovery: Discovery) -> list[str]:
    return [str(e) for e in validate_all(d, discovery.components, discovery.apps).errors]


# ── The manifest and the validator ────────────────────────────────────


def test_the_edge_is_an_optional_module(discovery: Discovery) -> None:
    edge = _edge(discovery)
    assert edge.metadata.category == "optional"
    # Not enforced for an optional component, so it must not be declared.
    assert edge.spec.activation is None


def test_the_validator_accepts_it_with_the_gateway(discovery: Discovery) -> None:
    d = _minimal_compose(modules=EDGE_MODULES)
    assert not [m for m in _errors(d, discovery) if APP_EDGE_MODULE in m]


def test_the_validator_refuses_it_without_the_gateway(discovery: Discovery) -> None:
    d = _minimal_compose(modules=EDGE_MODULES)
    d.spec.auth.gateway = False
    d.spec.auth.mode = "api-key-only"
    msgs = [m for m in _errors(d, discovery) if APP_EDGE_MODULE in m]
    assert len(msgs) == 1
    assert "auth gateway" in msgs[0] and "remove-module app-edge" in msgs[0]


def test_the_validator_refuses_it_with_core_suppressed(discovery: Discovery) -> None:
    d = _minimal_compose(modules=EDGE_MODULES)
    d.spec.modules.suppress_core = True
    msgs = [m for m in _errors(d, discovery) if APP_EDGE_MODULE in m]
    assert len(msgs) == 1 and "core" in msgs[0]


# ── One source for "is the edge on" ───────────────────────────────────


@pytest.mark.parametrize("modules", [["mcp-server"], EDGE_MODULES, [APP_EDGE_MODULE]])
def test_every_consumer_follows_the_module(modules: list[str], discovery: Discovery) -> None:
    d = _minimal_compose(modules=modules)
    on = is_component_active(_edge(discovery), d)
    assert app_edge_active(d) is on
    assert (APP_EDGE_KEY in effective_api_keys(d)) is on
    policy = json.loads(attach_policy_json(d, discovery.components, discovery.apps))
    # Written only when false, so an older Registry can still read the file.
    assert policy.get("app_edge", True) is on
    assert ("app_edge" in policy) is (not on)


def test_module_off_renders_no_edge_and_apps_404(tmp_path: Path, discovery: Discovery) -> None:
    d = _minimal_compose(apps=["wip-kb"])
    assert d.spec.auth.gateway and APP_EDGE_MODULE not in d.spec.modules.optional
    tree = render_compose(d, discovery.components, discovery.apps, _compose_secrets(tmp_path, d, discovery))

    compose = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
    assert APP_EDGE_MODULE not in compose["services"]
    keys = json.loads(tree.files[Path("config/keys/api-keys.json")].content)["keys"]
    assert "wip-app-edge" not in {k["name"] for k in keys}

    caddyfile = tree.files[Path("config/caddy/Caddyfile")].content
    assert "handle /apps/* {" not in caddyfile
    assert "wip-app-edge" not in caddyfile
    # The deployer app's own route stays; any other /apps path reaches the
    # terminal 404, never Caddy's empty 200.
    assert "handle /apps/kb/* {" in caddyfile
    assert "    handle {\n        respond 404\n    }\n" in caddyfile

    policy = json.loads(tree.files[Path("config/auth/attach-policy.json")].content)
    assert policy["app_edge"] is False


def test_module_off_on_k8s_renders_no_edge_and_no_edge_policy(
    tmp_path: Path, discovery: Discovery,
) -> None:
    d = _k8s_deployment(apps=["wip-kb"])
    tree = render_k8s(d, discovery.components, discovery.apps, _k8s_secrets(tmp_path, d, discovery))
    assert Path("services/app-edge.yaml") not in tree.files
    policies = {
        doc["metadata"]["name"]
        for doc in yaml.safe_load_all(tree.files[Path("network-policies.yaml")].content)
        if doc
    }
    assert "app-edge-egress" not in policies
    caddy = yaml.safe_load_all(tree.files[Path("services/caddy.yaml")].content)
    caddyfile = next(x for x in caddy if x["kind"] == "ConfigMap")["data"]["Caddyfile"]
    assert "handle /apps/* {" not in caddyfile
    assert "    handle {\n        respond 404\n    }\n" in caddyfile


def test_module_on_renders_todays_edge(tmp_path: Path, discovery: Discovery) -> None:
    d = _minimal_compose(modules=EDGE_MODULES, apps=["wip-kb"])
    tree = render_compose(d, discovery.components, discovery.apps, _compose_secrets(tmp_path, d, discovery))
    compose = yaml.safe_load(tree.files[Path("docker-compose.yaml")].content)
    assert compose["services"][APP_EDGE_MODULE]["container_name"] == "wip-app-edge"
    assert "reverse_proxy wip-app-edge:8080" in tree.files[Path("config/caddy/Caddyfile")].content
    policy = json.loads(tree.files[Path("config/auth/attach-policy.json")].content)
    assert "app_edge" not in policy


# ── Presets and install flags ─────────────────────────────────────────


def _inputs(**overrides: object) -> BuildInputs:
    base: dict[str, object] = {
        "name": "t", "preset": "standard", "target": "compose",
        "hostname": "wip.local", "tls": "internal",
        "compose_data_dir": Path("/tmp/wip-data"),
    }
    base.update(overrides)
    return BuildInputs(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_exactly_the_gateway_presets_include_it(preset: str) -> None:
    p = PRESETS[preset]
    listed = APP_EDGE_MODULE in p.get("modules", {}).get("optional", [])
    assert listed is bool(p.get("auth", {}).get("gateway"))


def test_no_auth_gateway_drops_the_presets_edge_with_a_note() -> None:
    inputs = _inputs(auth_gateway=False, auth_mode="api-key-only")
    d = build_deployment(inputs)
    assert APP_EDGE_MODULE not in d.spec.modules.optional
    assert preset_edge_dropped(inputs, d)

    on = _inputs()
    assert APP_EDGE_MODULE in build_deployment(on).spec.modules.optional
    assert not preset_edge_dropped(on, build_deployment(on))


def test_an_explicit_add_stays_for_the_validator_to_refuse(discovery: Discovery) -> None:
    inputs = _inputs(auth_gateway=False, auth_mode="api-key-only", add=[APP_EDGE_MODULE])
    d = build_deployment(inputs)
    assert APP_EDGE_MODULE in d.spec.modules.optional
    assert [m for m in _errors(d, discovery) if APP_EDGE_MODULE in m]


def test_remove_flag_is_not_reported_as_a_drop() -> None:
    inputs = _inputs(remove=[APP_EDGE_MODULE])
    d = build_deployment(inputs)
    assert APP_EDGE_MODULE not in d.spec.modules.optional
    assert not preset_edge_dropped(inputs, d)


# ── The one-time saved-spec migration ─────────────────────────────────


def _v1(*, gateway: bool, suppress_core: bool = False, optional: list[str] | None = None) -> dict:
    return {
        "wip_deploy_format_version": 1,
        "deployment": {
            "metadata": {"name": "old"},
            "spec": {
                "target": "compose",
                "apps": [],
                "modules": {"optional": optional or ["mcp-server"], "suppress_core": suppress_core},
                "auth": (
                    {"mode": "hybrid", "gateway": True}
                    if gateway
                    else {"mode": "api-key-only", "gateway": False, "users": []}
                ),
                "network": {"hostname": "localhost"},
                "images": {},
                "platform": {"compose": {"data_dir": "/tmp/d"}},
                "secrets": {"backend": "file", "location": "/tmp/s"},
                "apply": {},
            },
        },
    }


def test_v1_with_the_gateway_keeps_its_edge() -> None:
    out = _upgrade_state_payload(_v1(gateway=True))
    assert out["wip_deploy_format_version"] == 2
    assert out["deployment"]["spec"]["modules"]["optional"] == ["mcp-server", APP_EDGE_MODULE]


@pytest.mark.parametrize("kw", [{"gateway": False}, {"gateway": True, "suppress_core": True}])
def test_v1_without_an_edge_gets_none(kw: dict) -> None:
    out = _upgrade_state_payload(_v1(**kw))
    assert out["wip_deploy_format_version"] == 2
    assert APP_EDGE_MODULE not in out["deployment"]["spec"]["modules"]["optional"]


def test_the_upgrade_does_not_mutate_its_input_and_does_not_duplicate() -> None:
    raw = _v1(gateway=True, optional=["mcp-server", APP_EDGE_MODULE])
    before = json.dumps(raw, sort_keys=True)
    out = _state_v1_to_v2(raw)
    assert json.dumps(raw, sort_keys=True) == before
    assert out["deployment"]["spec"]["modules"]["optional"].count(APP_EDGE_MODULE) == 1


def test_a_v2_spec_without_the_edge_stays_without_it() -> None:
    payload = _v1(gateway=True)
    payload["wip_deploy_format_version"] = 2
    assert _upgrade_state_payload(payload) == payload


def test_an_unknown_version_is_left_for_the_loader_to_refuse(tmp_path: Path) -> None:
    payload = _v1(gateway=True)
    payload["wip_deploy_format_version"] = 99
    assert _upgrade_state_payload(payload) == payload
    (tmp_path / "deployment.deployer-state").write_text(json.dumps(payload))
    assert _try_load_previous_deployment(tmp_path) is None


def test_both_loaders_upgrade_and_the_next_persist_writes_v2(tmp_path: Path) -> None:
    (tmp_path / "deployment.deployer-state").write_text(json.dumps(_v1(gateway=True)))
    loaded = _load_deployment(tmp_path)
    assert APP_EDGE_MODULE in loaded.spec.modules.optional
    previous = _try_load_previous_deployment(tmp_path)
    assert previous is not None and APP_EDGE_MODULE in previous.spec.modules.optional

    _persist_deployment(loaded, tmp_path)
    saved = json.loads((tmp_path / "deployment.deployer-state").read_text())
    assert saved["wip_deploy_format_version"] == 2
    assert APP_EDGE_MODULE in saved["deployment"]["spec"]["modules"]["optional"]

    # The operator removes it; it stays removed on the next load.
    loaded.spec.modules.optional.remove(APP_EDGE_MODULE)
    _persist_deployment(loaded, tmp_path)
    assert APP_EDGE_MODULE not in _load_deployment(tmp_path).spec.modules.optional


# ── remove-module app-edge ────────────────────────────────────────────


def test_remove_module_app_edge_warns_about_attached_apps(tmp_path: Path) -> None:
    payload = _v1(gateway=True, optional=["mcp-server", APP_EDGE_MODULE])
    payload["wip_deploy_format_version"] = 2
    (tmp_path / "deployment.deployer-state").write_text(json.dumps(payload))
    with (
        patch("wip_deploy.apply.stop_and_remove_container") as stop,
        patch("wip_deploy.cli._apply_and_persist_mutation") as apply,
    ):
        result = runner.invoke(
            app, ["remove-module", APP_EDGE_MODULE, "--install-dir", str(tmp_path)]
        )
    assert result.exit_code == 0, result.output
    assert "attached through the Registry's API go offline" in result.output
    assert "add-module app-edge" in result.output
    stop.assert_called_once_with(APP_EDGE_MODULE)
    applied = apply.call_args.args[0]
    assert APP_EDGE_MODULE not in applied.spec.modules.optional
