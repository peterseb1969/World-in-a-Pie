"""An apply refuses to reserve a namespace a runtime API key already reserves.

The Registry can hold reservations made through its API, for apps wip-deploy
does not manage. One prefix has one owner, so an apply asks the running
Registry for them and refuses a collision on a namespace or an ID prefix. An
unreachable or older Registry is a note, not a stop: the Registry refuses a
colliding prefix at the namespace upsert itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.discovery import Discovery, discover
from wip_deploy.runtime_reservations import check_runtime_reservations, collisions
from wip_deploy.spec import App, AppRef, Deployment

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def discovery() -> Discovery:
    return discover(REPO_ROOT)


def _app(disc: Discovery, name: str, namespaces: list[dict[str, Any]]) -> App:
    base = next(a for a in disc.apps if a.metadata.name == "wip-cooking")
    raw = base.model_dump()
    raw["metadata"]["name"] = name
    raw["spec"]["env"]["required"] = [
        e for e in raw["spec"]["env"]["required"] if e["name"] != "WIP_NAMESPACE"
    ]
    raw["spec"]["namespaces"] = namespaces
    return App.model_validate(raw)


def _deployment(apps: list[App]) -> Deployment:
    d = build_deployment(BuildInputs(
        target="compose", variant="prod", hostname="wip.example.org", tls="internal",
        registry="ghcr.io/test", compose_data_dir=Path("/tmp/d"), k8s_namespace="wip-t",
    ))
    d.spec.apps = [AppRef(name=a.metadata.name) for a in apps]
    return d


def _transport(status: int, body: Any, seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)
    return httpx.MockTransport(handler)


def test_a_namespace_a_runtime_key_reserves_is_refused(discovery: Discovery):
    app = _app(discovery, "cook", [{"role": "main", "name": "cook-data", "env": "WIP_NAMESPACE"}])
    seen: list[httpx.Request] = []
    result = check_runtime_reservations(
        _deployment([app]), [app], "admin-key",
        transport=_transport(200, [{"prefix": "cook-data", "app": "ext-key", "settings": {}}], seen),
    )
    assert len(result.errors) == 1
    assert "runtime API key 'ext-key' already reserves it" in result.errors[0]
    assert seen[0].url.path == "/api/registry/reservations"
    assert seen[0].url.params["source"] == "runtime"
    assert seen[0].headers["X-API-Key"] == "admin-key"


def test_no_collision_passes(discovery: Discovery):
    app = _app(discovery, "cook", [{"role": "main", "name": "cook-data", "env": "WIP_NAMESPACE"}])
    result = check_runtime_reservations(
        _deployment([app]), [app], "admin-key",
        transport=_transport(200, [{"prefix": "other", "app": "ext-key", "settings": {}}], []),
    )
    assert result.errors == [] and result.note is None


def test_an_id_prefix_collision_is_refused():
    ids = {"id_config": {"terms": {"algorithm": "prefixed", "prefix": "CK-"}}}
    errors = collisions(
        [{"prefix": "cook", "app": "cooking", "settings": ids}],
        [{"prefix": "ext", "app": "ext-key", "settings": ids}],
    )
    assert len(errors) == 1
    assert "ID prefix 'CK-'" in errors[0] and "ext.terms (runtime key 'ext-key')" in errors[0]


@pytest.mark.parametrize("status,needle", [(404, "predates"), (503, "answered 503")])
def test_an_older_or_failing_registry_is_a_note(discovery: Discovery, status: int, needle: str):
    app = _app(discovery, "cook", [{"role": "main", "name": "cook-data", "env": "WIP_NAMESPACE"}])
    result = check_runtime_reservations(
        _deployment([app]), [app], "admin-key", transport=_transport(status, {}, []),
    )
    assert result.errors == []
    assert needle in (result.note or "")


def test_an_unreachable_registry_is_a_note(discovery: Discovery):
    app = _app(discovery, "cook", [{"role": "main", "name": "cook-data", "env": "WIP_NAMESPACE"}])

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = check_runtime_reservations(
        _deployment([app]), [app], "admin-key", transport=httpx.MockTransport(refuse),
    )
    assert result.errors == []
    assert "not reachable" in (result.note or "")


def test_no_reservations_means_no_request(discovery: Discovery):
    app = _app(discovery, "cook", [])
    seen: list[httpx.Request] = []
    result = check_runtime_reservations(
        _deployment([app]), [app], "admin-key", transport=_transport(200, [], seen),
    )
    assert seen == [] and result.errors == [] and result.note is None


def test_an_apply_stops_on_a_collision_and_proceeds_on_a_note(discovery: Discovery, capsys):
    from unittest.mock import patch

    import typer

    from wip_deploy.cli import _refuse_runtime_reservation_collisions
    from wip_deploy.runtime_reservations import RuntimeReservationCheck
    from wip_deploy.secrets_backend import ResolvedSecrets

    app = _app(discovery, "cook", [{"role": "main", "name": "cook-data", "env": "WIP_NAMESPACE"}])
    d, secrets = _deployment([app]), ResolvedSecrets(values={"api-key": "k"})
    target = "wip_deploy.runtime_reservations.check_runtime_reservations"

    collision = RuntimeReservationCheck(errors=["namespace 'cook-data' ..."])
    with patch(target, return_value=collision), pytest.raises(typer.Exit) as exc:
        _refuse_runtime_reservation_collisions(d, [app], secrets)
    assert exc.value.exit_code == 2
    assert "collide with runtime reservations" in capsys.readouterr().err

    with patch(target, return_value=RuntimeReservationCheck(note="not reachable")):
        _refuse_runtime_reservation_collisions(d, [app], secrets)
    assert "note: not reachable" in capsys.readouterr().err
