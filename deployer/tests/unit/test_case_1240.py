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


def test_the_edge_key_is_one_of_the_reserved_names() -> None:
    assert api_keys.APP_EDGE_KEY.name == APP_EDGE_KEY_NAME
    assert set(SYSTEM_KEY_NAMES) == {CANARY_KEY_NAME, APP_EDGE_KEY_NAME}


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
    with pytest.raises(ValueError, match=f"name {CANARY_KEY_NAME} twice"):
        api_keys.effective_api_keys(_edge_install(), [])


def test_an_edge_install_renders_each_name_once() -> None:
    names = [k.name for k in api_keys.effective_api_keys(_edge_install(), [])]
    assert APP_EDGE_KEY_NAME in names and len(names) == len(set(names))


def _client(status: int, body: dict | None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/registry/health"
        return httpx.Response(status, json=body) if body is not None else httpx.Response(status)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_refused_key_file_fails_the_live_check() -> None:
    r = check_key_file_imported("https://wip.example.org", _client(200, {"status": "healthy", "key_file_refused": True}))
    assert not r.passed and "refused the key file" in r.message


def test_an_imported_key_file_passes() -> None:
    r = check_key_file_imported("https://wip.example.org", _client(200, {"status": "healthy", "key_file_refused": False}))
    assert r.passed and r.message == "imported"


def test_an_older_registry_is_named_not_failed() -> None:
    r = check_key_file_imported("https://wip.example.org", _client(200, {"status": "healthy"}))
    assert r.passed and "predates" in r.message


def test_an_unhealthy_registry_fails() -> None:
    r = check_key_file_imported("https://wip.example.org", _client(503, {"status": "unhealthy"}))
    assert not r.passed and "503" in r.message
