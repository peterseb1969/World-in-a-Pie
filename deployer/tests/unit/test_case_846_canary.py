"""Every install's canary key and the live authorization probe (CASE-846).

The deployer adds a key that authenticates but is authorized for nothing
to every install. `verify --security --live` sends it to each service's
privileged and namespace-scoped routes and requires a refusal, with the
admin key as the positive control. These tests pin the probe's verdicts
through an httpx.MockTransport standing in for the install, and the
canary's place in the spec.
"""

from __future__ import annotations

import httpx
import pytest

from wip_deploy.config_gen.api_keys import CANARY_KEY, CANARY_NAMESPACE
from wip_deploy.spec.deployment import CANARY_KEY_NAME, AuthSpec, SpecAPIKey
from wip_deploy.verify_security import (
    AUTHZ_PROBES,
    check_authorization_refusal,
    read_canary_key,
)

BASE = "https://wip.example"
ALL = {p.component for p in AUTHZ_PROBES}
ADMIN = "admin-key"
CANARY = "canary-key"
ADMIN_STATUS = {p.path: p.admin_status for p in AUTHZ_PROBES}


def _json(status: int, body=None) -> httpx.Response:
    return httpx.Response(status, json=body if body is not None else {"detail": "x"})


def _enforcing(request: httpx.Request) -> httpx.Response:
    """An install that enforces: the canary is refused, the admin passes."""
    path = request.url.path
    key = request.headers.get("X-API-Key")
    if key == ADMIN:
        if path == "/api/registry/entries":
            return _json(200, {"items": [{}], "total": 7})
        return _json(ADMIN_STATUS[request.url.raw_path.decode()], {"ok": True})
    if key == CANARY:
        if path == "/api/registry/my/namespaces":
            return _json(200, [])
        if path == "/api/registry/entries":
            return _json(200, {"items": [], "total": 0})
        return _json(403)
    return _json(401)


def _run(handler, active=ALL, canary=CANARY, admin=ADMIN):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return check_authorization_refusal(BASE, active, canary, admin, client)


def test_an_enforcing_install_passes():
    result = _run(_enforcing)
    assert result.passed, result.fix_hint


def test_an_unknown_canary_is_a_failure_not_a_refusal():
    """401 means the service never loaded the canary; every refusal on it
    would pass without testing anything."""
    def handler(request):
        if request.url.path.startswith("/api/def-store/") and request.headers.get("X-API-Key") == CANARY:
            return _json(401)
        return _enforcing(request)

    result = _run(handler)
    assert not result.passed
    assert "does not recognise the canary" in result.fix_hint


def test_a_privileged_route_admitting_the_canary_fails():
    """The CASE-844 shape: the gate never ran, the canary reached the handler."""
    def handler(request):
        if request.url.path == "/api/registry/entries/register":
            return _json(422)
        return _enforcing(request)

    result = _run(handler)
    assert not result.passed
    assert "POST /api/registry/entries/register: canary got 422" in result.fix_hint


def test_a_missing_route_fails_on_the_admin_control():
    """A 404 to the canary proves nothing if the route does not exist: the
    admin key must reach the handler."""
    def handler(request):
        if request.url.path == "/api/template-store/health/events":
            return _json(404)
        return _enforcing(request)

    result = _run(handler)
    assert not result.passed
    assert "admin key got 404" in result.fix_hint


def test_a_canary_that_sees_a_namespace_fails():
    def handler(request):
        if request.url.path == "/api/registry/my/namespaces" and request.headers.get("X-API-Key") == CANARY:
            return _json(200, [{"prefix": "wip"}])
        return _enforcing(request)

    result = _run(handler)
    assert not result.passed
    assert "the canary can access ['wip']" in result.fix_hint


def test_a_canary_that_lists_registry_entries_fails():
    def handler(request):
        if request.url.path == "/api/registry/entries" and request.headers.get("X-API-Key") == CANARY:
            return _json(200, {"items": [{}], "total": 3})
        return _enforcing(request)

    assert not _run(handler).passed


def test_without_keys_the_check_cannot_pass():
    assert not _run(_enforcing, canary=None).passed
    assert not _run(_enforcing, admin=None).passed


def test_inactive_components_are_not_probed():
    seen: set[str] = set()

    def handler(request):
        seen.add(request.url.path.split("/")[2])
        return _enforcing(request)

    assert _run(handler, active={"def-store"}).passed
    assert seen == {"def-store"}


def test_write_probes_send_an_invalid_body():
    """A write probe must not be able to write even where the permission
    check is missing: its body fails validation, so an open gate answers
    422 and nothing is stored."""
    for probe in AUTHZ_PROBES:
        if probe.method == "GET":
            assert probe.body is None, probe
            continue
        assert isinstance(probe.body, str), probe
        assert probe.admin_status == 422, probe


def test_the_canary_is_authorized_for_nothing():
    assert CANARY_KEY.name == CANARY_KEY_NAME
    assert CANARY_KEY.groups == []
    assert CANARY_KEY.grants == {}
    assert CANARY_KEY.namespaces == [CANARY_NAMESPACE]


def test_a_spec_cannot_declare_the_canary_name():
    with pytest.raises(ValueError, match="reserved"):
        AuthSpec(
            mode="api-key-only",
            gateway=False,
            api_keys=[SpecAPIKey(name=CANARY_KEY_NAME, namespaces=["kb"])],
        )


def test_the_canary_key_is_read_from_the_file_backend(tmp_path):
    from wip_deploy.build import BuildInputs, build_deployment

    d = build_deployment(BuildInputs(
        target="compose", variant="dev", hostname="wip.example", tls="internal",
        compose_data_dir=tmp_path / "data",
    ))
    secrets = d.spec.secrets.model_copy(update={"location": str(tmp_path / "secrets")})
    d = d.model_copy(update={"spec": d.spec.model_copy(update={"secrets": secrets})})
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / CANARY_KEY.secret_name).write_text("canary\n")
    assert read_canary_key(tmp_path, d) == "canary"


def test_the_namespace_upsert_probe_sends_the_canary_a_valid_body():
    """The upsert checks permission inside its handler (it admits keys that
    hold a namespace reservation), so an invalid body is answered 422 for
    every key. The canary must send a valid one to reach the check."""
    seen = {}

    def handler(request):
        if request.url.path == "/api/registry/namespaces/wip-canary-probe":
            seen[request.headers.get("X-API-Key")] = request.read()
            if request.headers.get("X-API-Key") == CANARY:
                return _json(403) if request.read() == b"{}" else _json(422)
        return _enforcing(request)

    result = _run(handler)
    assert result.passed, result.fix_hint
    assert seen[CANARY] == b"{}"
    assert seen[ADMIN] != b"{}"


def test_an_upsert_admitting_the_canary_names_what_it_created():
    def handler(request):
        if (request.url.path == "/api/registry/namespaces/wip-canary-probe"
                and request.headers.get("X-API-Key") == CANARY):
            return _json(200, {"prefix": "wip-canary-probe"})
        return _enforcing(request)

    result = _run(handler)
    assert not result.passed
    assert "it may have written namespace 'wip-canary-probe', remove it" in result.fix_hint
