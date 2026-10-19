"""`wip-deploy verify --security --live` (CASE-851, Phase 2b C9).

The live checks ask the running install whether its services refuse
requests without credentials, and whether reporting-sync runs on its
unprivileged PostgreSQL roles. These tests drive them through an
httpx.MockTransport standing in for the install.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from wip_deploy.build import BuildInputs, build_deployment
from wip_deploy.verify_security import (
    ANONYMOUS_PROBES,
    AUTHZ_PROBES,
    PUBLIC_PROBES,
    check_anonymous_refusal,
    check_reporting_roles,
    read_admin_key,
    verify_live,
)

BASE = "https://wip.example"
ALL = {p.component for p in ANONYMOUS_PROBES}
ADMIN = "admin-key"
CANARY = "canary-key"
PUBLIC_PATHS = {p.path for p in PUBLIC_PROBES}
ADMIN_STATUS = {p.path: p.admin_status for p in AUTHZ_PROBES}


def _json(status: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json=body or {"detail": "x"})


def _secure_install(request: httpx.Request) -> httpx.Response:
    """An install where everything protected refuses and probes answer:
    no key gets 401, the canary is authenticated but refused, the admin key
    reaches every handler."""
    path = request.url.path
    target = request.url.raw_path.decode()
    key = request.headers.get("X-API-Key")
    if path in PUBLIC_PATHS:
        return _json(200, {"status": "healthy"})
    if key == ADMIN:
        if path == "/api/reporting-sync/status":
            return _json(200, {"postgres_service_roles_ok": True})
        if path == "/api/registry/entries":
            return _json(200, {"items": [{}], "total": 7})
        return _json(ADMIN_STATUS.get(target, 200), {"ok": True})
    if key == CANARY:
        if path == "/api/registry/my/namespaces":
            return httpx.Response(200, json=[])
        if path == "/api/registry/entries":
            return _json(200, {"items": [], "total": 0})
        return _json(403)
    return _json(401)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_secure_install_passes():
    result = check_anonymous_refusal(BASE, ALL, _client(_secure_install))
    assert result.passed, result.fix_hint


def test_a_route_served_without_credentials_fails():
    def handler(request):
        if request.url.path == "/api/reporting-sync/query":
            return _json(200, {"rows": [{"?column?": 1}]})
        return _secure_install(request)

    result = check_anonymous_refusal(BASE, ALL, _client(handler))
    assert not result.passed
    assert "POST /api/reporting-sync/query: 200 — served without credentials" in result.fix_hint


def test_spa_fallback_is_not_mistaken_for_a_refusal():
    """An unserved route answers 200 HTML from the SPA fallback: that says
    nothing about authentication and must fail, not pass."""
    def handler(request):
        if request.url.path.startswith("/api/ingest-gateway/") and request.url.path not in PUBLIC_PATHS:
            return httpx.Response(200, text="<!doctype html><html></html>",
                                  headers={"content-type": "text/html"})
        return _secure_install(request)

    result = check_anonymous_refusal(BASE, ALL, _client(handler))
    assert not result.passed
    assert "not served by this install" in result.fix_hint


def test_a_gated_health_probe_fails():
    def handler(request):
        if request.url.path == "/api/registry/health":
            return _json(401)
        return _secure_install(request)

    result = check_anonymous_refusal(BASE, ALL, _client(handler))
    assert not result.passed
    assert "/api/registry/health" in result.fix_hint


def test_inactive_components_are_not_probed():
    seen: list[str] = []

    def handler(request):
        seen.append(request.url.path)
        return _secure_install(request)

    result = check_anonymous_refusal(BASE, {"registry"}, _client(handler))
    assert result.passed
    assert all(path.startswith("/api/registry/") for path in seen)


def test_no_active_component_cannot_pass():
    assert not check_anonymous_refusal(BASE, set(), _client(_secure_install)).passed


def test_probes_never_write():
    """Probes must stay harmless against an install whose auth is broken."""
    for probe in ANONYMOUS_PROBES:
        assert probe.method in ("GET", "POST"), probe
        if probe.method == "POST":
            assert probe.path.endswith(("/query", "/search")), probe


def test_roles_check_reads_status_with_the_admin_key():
    assert check_reporting_roles(BASE, ADMIN, ALL, _client(_secure_install)).passed


def test_roles_check_fails_when_roles_are_not_ok():
    def handler(request):
        return _json(200, {"postgres_service_roles_ok": False})

    result = check_reporting_roles(BASE, ADMIN, ALL, _client(handler))
    assert not result.passed
    assert "False" in result.message


def test_roles_check_without_a_key_cannot_pass():
    assert not check_reporting_roles(BASE, None, ALL, _client(_secure_install)).passed


def test_roles_check_not_applicable_without_reporting_sync():
    result = check_reporting_roles(BASE, None, {"registry"}, _client(_secure_install))
    assert result.passed
    assert "not applicable" in result.message


def _deployment(tmp_path: Path):
    d = build_deployment(BuildInputs(
        target="compose", variant="dev", hostname="wip.example", tls="internal",
        compose_data_dir=tmp_path / "data",
    ))
    secrets = d.spec.secrets.model_copy(update={"location": str(tmp_path / "secrets")})
    return d.model_copy(update={"spec": d.spec.model_copy(update={"secrets": secrets})})


def test_verify_live_targets_the_public_base_url(tmp_path: Path):
    deployment = _deployment(tmp_path)
    hosts: set[str] = set()

    def handler(request):
        hosts.add(f"{request.url.scheme}://{request.url.host}:{request.url.port}")
        return _secure_install(request)

    results = verify_live(
        deployment, ALL, ADMIN, canary_key=CANARY, transport=httpx.MockTransport(handler)
    )
    assert [r.passed for r in results] == [True, True, True], [r.fix_hint for r in results]
    assert hosts == {f"https://wip.example:{deployment.spec.network.https_port}"}


def test_admin_key_is_read_from_the_file_backend(tmp_path: Path):
    deployment = _deployment(tmp_path)
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "api-key").write_text("from-backend\n")
    assert read_admin_key(tmp_path, deployment) == "from-backend"


def test_certificates_are_only_verified_for_letsencrypt(tmp_path: Path, monkeypatch):
    """Internal and self-signed installs present certificates this machine
    does not trust; the live check is about authentication and must still
    run against them (found against a real self-signed k8s install)."""
    import wip_deploy.verify_security as vs

    seen: list[bool] = []
    real_client = httpx.Client

    def recording_client(*args, **kwargs):
        seen.append(kwargs.get("verify"))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", recording_client)
    base = _deployment(tmp_path)
    for tls in ("internal", "self-signed"):
        # model_copy skips validation: self-signed is a k8s-only mode, and
        # only the TLS mode matters to this check.
        network = base.spec.network.model_copy(update={"tls": tls})
        deployment = base.model_copy(
            update={"spec": base.spec.model_copy(update={"network": network})}
        )
        vs.verify_live(deployment, ALL, ADMIN,
                       transport=httpx.MockTransport(_secure_install))
    assert seen == [False, False]
