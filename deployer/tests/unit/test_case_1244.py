"""The apps-only install shape is retired.

An apps-only install ran an app's containers with no WIP backend of its own,
against a remote WIP: `--apps-only` (spec `modules.suppress_core`),
`--remote-wip` (`network.remote_wip_url`) and `import-bundle` (the remote's key
and CA, mounted into the apps through `network.external_ca_mount`). It was the
only shape with no login in front of its apps and the only one whose apps ran on
the install's `api-key` rather than a key of their own. An app that runs away
from its WIP is attached to it through the app edge instead.

What stays, and is pinned here: an app reads the WIP through this install's own
router and public URL, with its own derived key, and core is always active.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.cli import (
    _DEPLOYMENT_JSON_VERSION,
    RetiredInstallShape,
    _load_deployment,
    _previous_deployment_or_unreadable,
    _upgrade_state_payload,
    app,
)
from wip_deploy.config_gen import make_spec_context, resolve_from_spec
from wip_deploy.config_gen.env import Literal, resolve_all_env
from wip_deploy.discovery import discover
from wip_deploy.spec.activation import is_component_active
from wip_deploy.spec.component import ActivationSpec
from wip_deploy.spec.deployment import ModulesSpec, NetworkSpec

REPO_ROOT = Path(__file__).resolve().parents[3]
runner = CliRunner()


@pytest.fixture(scope="module")
def discovery():
    return discover(REPO_ROOT)


def _deployment(apps: list[str] | None = None):
    return build_deployment(BuildInputs(
        name="t", preset="standard", target="compose", hostname="wip-pi.local",
        compose_data_dir=Path("/tmp/wip-1244"), apps=apps or [],
    ))


# ── the shape is gone from the spec and the CLI ─────────────────────────


@pytest.mark.parametrize("model,field", [
    (ModulesSpec, "suppress_core"),
    (NetworkSpec, "remote_wip_url"),
    (NetworkSpec, "external_ca_mount"),
    (ActivationSpec, "requires_core"),
])
def test_the_spec_has_no_apps_only_field(model, field: str) -> None:
    assert field not in model.model_fields


@pytest.mark.parametrize("flag", ["--apps-only", "--remote-wip"])
def test_install_has_no_apps_only_flag(flag: str) -> None:
    r = runner.invoke(app, ["install", flag])
    assert r.exit_code == 2 and "No such option" in r.output


def test_import_bundle_is_not_a_verb() -> None:
    r = runner.invoke(app, ["import-bundle", "--help"])
    assert r.exit_code == 2 and "No such command" in r.output


# ── what stays: core always runs, apps use this install's router and key ─


def test_core_and_its_infrastructure_are_always_active(discovery) -> None:
    d = _deployment()
    for name in ("registry", "def-store", "template-store", "document-store", "mongodb", "router"):
        component = next(c for c in discovery.components if c.metadata.name == name)
        assert is_component_active(component, d), name


def test_external_base_url_is_this_installs_public_url() -> None:
    ctx = make_spec_context(_deployment(), [])
    assert ctx.network.external_base_url == "https://wip-pi.local:8443"
    assert resolve_from_spec("network.external_base_url", ctx) == "https://wip-pi.local:8443"


def test_an_app_reaches_the_wip_through_the_router_with_its_own_key(discovery) -> None:
    d = _deployment(apps=["wip-cooking"])
    ctx = make_spec_context(d, discovery.components)
    env = resolve_all_env(d, discovery.components, discovery.apps, ctx)["wip-cooking"].required
    base = env["WIP_BASE_URL"]
    assert isinstance(base, Literal) and base.value == "http://wip-router:8080"
    assert env["WIP_API_KEY"].name == "app-wip-cooking-api-key"


# ── saved state: format 5 drops the defaults and refuses an apps-only install ─


def _v4_state(*, suppress_core: bool = False, remote: str | None = None,
              ca_mount: bool = False) -> dict:
    payload = {"wip_deploy_format_version": 4, "deployment": _deployment().model_dump(mode="json")}
    spec = payload["deployment"]["spec"]
    spec["modules"]["suppress_core"] = suppress_core
    spec["network"]["remote_wip_url"] = remote
    spec["network"]["external_ca_mount"] = ca_mount
    return payload


def test_a_full_installs_defaults_are_dropped(tmp_path: Path) -> None:
    out = _upgrade_state_payload(_v4_state())
    assert out["wip_deploy_format_version"] == _DEPLOYMENT_JSON_VERSION == 5
    assert "suppress_core" not in out["deployment"]["spec"]["modules"]
    assert not {"remote_wip_url", "external_ca_mount"} & set(out["deployment"]["spec"]["network"])
    (tmp_path / "deployment.deployer-state").write_text(json.dumps(_v4_state()))
    assert _load_deployment(tmp_path).spec.network.hostname == "wip-pi.local"


@pytest.mark.parametrize("kw", [
    {"suppress_core": True},
    {"remote": "https://cloud.example"},
    {"suppress_core": True, "remote": "https://cloud.example", "ca_mount": True},
])
def test_an_apps_only_install_is_refused_with_the_attach_path(
    tmp_path: Path, kw: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(RetiredInstallShape, match="no longer supports"):
        _upgrade_state_payload(_v4_state(**kw))

    (tmp_path / "deployment.deployer-state").write_text(json.dumps(_v4_state(**kw)))
    with pytest.raises(typer.Exit) as exc:
        _load_deployment(tmp_path)
    assert exc.value.exit_code == 2
    err = capsys.readouterr().err
    assert "apps-only" in err and "attachments" in err

    previous, why = _previous_deployment_or_unreadable(tmp_path)
    assert previous is None and "apps-only" in (why or "")


def test_a_stray_ca_flag_on_a_full_install_is_dropped() -> None:
    # Set from a file at each render, never chosen: nothing to refuse.
    out = _upgrade_state_payload(_v4_state(ca_mount=True))
    assert "external_ca_mount" not in out["deployment"]["spec"]["network"]
