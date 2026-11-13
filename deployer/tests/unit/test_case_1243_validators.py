"""CASE-1243, spec and validation findings: each silent acceptance now refuses.

- 1-spec R4: a REQUIRED env var whose from_component* target the install does
  not run surfaced as a traceback at env resolution; validation now names it.
- 1-spec R6: a registry with a space inside was accepted, though its message
  says spaces are what it prevents.
- 1-spec R8: `activation` on a core or optional component was silently ignored.
- 1-spec R9: two component manifests of one name silently replaced each other.
- 6-render R7: `image.build_context`'s value was ignored; only `.` is honest.
- 4-config R2: a Dex client for a manifest with an empty description raised.
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.config_gen.dex import generate_dex_config
from wip_deploy.discovery import discover
from wip_deploy.spec.app import App
from wip_deploy.spec.component import Component, ImageRef
from wip_deploy.spec.deployment import ImagesSpec
from wip_deploy.spec.validators import validate_all

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def disc():
    return discover(REPO_ROOT)


def _component(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / f"components/{name}/wip-component.yaml").read_text())


# ── R4 ───────────────────────────────────────────────────────────────────


def test_a_required_reference_to_a_component_the_install_does_not_run_is_refused(disc) -> None:
    raw = yaml.safe_load((REPO_ROOT / "apps/wip-cooking/wip-app.yaml").read_text())
    raw["spec"]["env"]["required"].append({"name": "FILES", "source": {"from_component": "garage"}})
    app = App.model_validate(raw)
    apps = [a for a in disc.apps if a.metadata.name != "wip-cooking"] + [app]
    d = build_deployment(BuildInputs(name="t", preset="analytics", target="compose", hostname="wip.example.org",
                                     compose_data_dir=Path("/tmp/x"), apps=["wip-cooking"]))
    errors = [str(e) for e in validate_all(d, disc.components, apps).errors]
    assert any("'wip-cooking' requires env 'FILES' from 'garage'" in e for e in errors), errors

    d.spec.modules.optional = [*d.spec.modules.optional, "garage"]
    assert not any("requires env" in str(e) for e in validate_all(d, disc.components, apps).errors)


# ── R6 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("registry", ["ghcr.io /wip", "ghcr.io/ wip", "ghcr.io\t/wip"])
def test_a_registry_with_a_space_inside_is_refused(registry: str) -> None:
    with pytest.raises(ValidationError, match="spaces"):
        ImagesSpec(registry=registry)


def test_a_clean_registry_is_kept() -> None:
    assert ImagesSpec(registry="localhost:5000/wip").registry == "localhost:5000/wip"


# ── R8 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["registry", "mcp-server"])  # core, optional
def test_activation_on_a_core_or_optional_component_is_refused(name: str) -> None:
    raw = _component(name)
    raw["spec"]["activation"] = {"requires_auth_gateway": True}
    with pytest.raises(ValidationError, match="activation is read only for category infrastructure"):
        Component.model_validate(raw)


def test_activation_on_infrastructure_is_kept() -> None:
    assert Component.model_validate(_component("auth-gateway")).spec.activation is not None


# ── R9 ───────────────────────────────────────────────────────────────────


def test_two_components_of_one_name_are_a_discovery_error(tmp_path: Path) -> None:
    for d in ("components/registry", "components/registry-copy"):
        (tmp_path / d).mkdir(parents=True)
        shutil.copy(REPO_ROOT / "components/registry/wip-component.yaml", tmp_path / d)
    result = discover(tmp_path)
    assert any("two component manifests are named 'registry'" in str(e) for e in result.errors)


# ── 6-render R7 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("ctx", ["./docker", "src", ".."])
def test_a_build_context_other_than_the_component_dir_is_refused(ctx: str) -> None:
    with pytest.raises(ValidationError, match="build context is always the component's own directory"):
        ImageRef(name="x", build_context=Path(ctx))


def test_build_context_dot_and_none_are_kept() -> None:
    assert ImageRef(name="x", build_context=Path(".")).build_context == Path(".")
    assert ImageRef(name="x").build_context is None


# ── 4-config R2 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("description", ["", "\n\n", "   "])
def test_a_dex_client_with_no_description_is_named_by_its_component(disc, description: str) -> None:
    comps = copy.deepcopy(disc.components)
    for c in comps:
        if c.spec.oidc_client is not None:
            c.metadata.description = description
    d = build_deployment(BuildInputs(name="t", preset="standard", target="compose", hostname="wip.example.org",
                                     compose_data_dir=Path("/tmp/x")))
    cfg = generate_dex_config(d, comps, disc.apps)
    owners = {c.metadata.name for c in comps if c.spec.oidc_client is not None}
    assert cfg is not None and owners
    assert {client.name for client in cfg.clients} >= owners
