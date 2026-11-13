"""The deployer never renders a key file that names a key twice, and says so
when the Registry refused one.

A spec key named `wip-app-edge` on an install running the app edge put two keys
of that name into the key file. The Registry refuses such a file whole and
imports none of it, while the apply reports success: no key added, removed or
narrowed since took effect. Every name the deployer provisions itself is now
reserved from one tuple, the render refuses a repeated name, and
`verify --security --live` reads the Registry's report of a refused file.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

import wip_deploy.config_gen.api_keys as api_keys
from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.spec.deployment import (
    APP_EDGE_KEY_NAME,
    APP_EDGE_MODULE,
    CANARY_KEY_NAME,
    SYSTEM_KEY_NAMES,
    AuthSpec,
    SpecAPIKey,
)
from wip_deploy.verify_security import check_key_file_imported


def test_the_reserved_names_are_the_system_keys_the_deployer_provisions() -> None:
    # SYSTEM_KEY_NAMES lives in the spec layer, which cannot import config_gen;
    # this pins it to the keys config_gen actually adds, so a system key added
    # there without its name here fails this test.
    provisioned = {
        k.name for k in api_keys.provisioned_api_keys(_edge_install(), [])
        if not k.owner.startswith("app:")
    }
    assert set(SYSTEM_KEY_NAMES) == provisioned


def test_a_system_key_added_without_reservation_is_refused_at_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wip_deploy.spec.validators import _validate_provisioned_key_names

    monkeypatch.setattr(api_keys, "CANARY_KEY", SpecAPIKey(
        name="wip-new-system", owner="system:new", namespaces=["wip-canary-probe"],
    ))
    d = _edge_install()
    d.spec.auth.api_keys = [SpecAPIKey(name="wip-new-system", owner="me", namespaces=["wip"])]
    [error] = _validate_provisioned_key_names(d, [])
    assert "wip-new-system" in str(error) and "system:new" in str(error)


def test_the_key_file_says_a_system_key_is_the_deployers() -> None:
    import json

    class AnySecret:
        def get(self, name: str) -> str:
            return "x"

    secrets = AnySecret()
    keys = json.loads(api_keys.generate_api_keys_json(_edge_install(), secrets, []))["keys"]
    edge = next(k for k in keys if k["name"] == APP_EDGE_KEY_NAME)
    assert "provisioned by wip-deploy" in edge["description"]
    assert "Spec-declared" not in edge["description"]


@pytest.mark.parametrize("name", SYSTEM_KEY_NAMES)
def test_a_spec_key_may_not_take_a_system_key_name(name: str) -> None:
    with pytest.raises(ValidationError, match="reserved"):
        AuthSpec(mode="hybrid", gateway=True, api_keys=[SpecAPIKey(name=name, owner="me", namespaces=["wip-canary-probe"])])


def _edge_install():
    d = build_deployment(BuildInputs(
        name="t", preset="standard", target="compose", hostname="wip.example.org",
        compose_data_dir=Path("/tmp/wip-1240"),
    ))
    d.spec.modules.optional = [*d.spec.modules.optional, APP_EDGE_MODULE]
    return d


def test_the_render_refuses_a_name_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    # A system key added to the render without its name reserved: the render
    # stops instead of handing the Registry a file it will refuse.
    monkeypatch.setattr(api_keys, "APP_EDGE_KEY", SpecAPIKey(name=CANARY_KEY_NAME, owner="system:x", namespaces=["wip-canary-probe"]))
    with pytest.raises(api_keys.DuplicateKeyName, match=f"name {CANARY_KEY_NAME} twice"):
        api_keys.effective_api_keys(_edge_install(), [])


def test_an_edge_install_renders_each_name_once() -> None:
    names = [k.name for k in api_keys.effective_api_keys(_edge_install(), [])]
    assert APP_EDGE_KEY_NAME in names and len(names) == len(set(names))


def _client(status: int, body: dict | None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/registry/health"
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)
    return httpx.Client(transport=httpx.MockTransport(handler))


def _check(body: dict):
    return check_key_file_imported("https://wip.example.org", _client(200, body))


def test_only_an_import_passes() -> None:
    r = _check({"status": "healthy", "key_file_import": "imported"})
    assert r.passed and r.message == "imported"


@pytest.mark.parametrize(("state", "says"), [
    ("refused", "refused the key file"),
    ("failed", "failed partway"),
    ("not_configured", "no key file path"),
    ("pending", "not finished"),
])
def test_every_other_state_fails_and_says_which(state: str, says: str) -> None:
    r = _check({"status": "healthy", "key_file_import": state})
    assert not r.passed and says in r.message


def test_a_registry_that_does_not_report_the_import_fails() -> None:
    # An install whose Registry image predates the answer may be running the
    # very refusal this check exists for; "cannot tell" is not a pass.
    r = _check({"status": "healthy"})
    assert not r.passed and "predates" in r.message and "roll the Registry" in r.message


def test_an_unhealthy_registry_fails() -> None:
    r = check_key_file_imported("https://wip.example.org", _client(503, {"status": "unhealthy"}))
    assert not r.passed and "503" in r.message
