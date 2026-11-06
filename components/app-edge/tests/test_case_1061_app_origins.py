"""Attached apps on origins of their own (CASE-1061).

With WIP_APP_ORIGINS=subdomain wip-caddy hands every <name>.<hostname> that
no deployer app owns to the edge. The edge then:

- matches each attachment by host as well as path, so one app's origin never
  serves another app;
- tells the gateway which origin the browser addressed (X-Forwarded-Host),
  and sends a login to the handshake on WIP's origin;
- removes the app session cookie, as it removes wip_session;
- answers wip-caddy's on-demand TLS question (/edge/ask) with 200 only for an
  active attachment's host.
"""

from __future__ import annotations

import dataclasses

import edge_sync as es


def _sub() -> tuple[str, str, str]:
    configs = es.self_test_configs()
    return configs["full-subdomain"], configs["empty-subdomain"], configs["unavailable-subdomain"]


def test_each_attachment_is_matched_by_host_and_path() -> None:
    full, _, _ = _sub()
    assert "\t@t0_all {\n\t\thost aa.wip.example\n\t\tpath /apps/aa/*\n\t}" in full
    assert "\t@t1_bare {\n\t\thost bb.wip.example\n\t\tpath /apps/bb\n\t}" in full
    assert "handle @t0_all {" in full
    # The bare origin goes to the app.
    assert "\t@t0_root {\n\t\thost aa.wip.example\n\t\tpath /\n\t}" in full
    assert "redir * /apps/aa/ permanent" in full
    # A path-only matcher would let any origin serve the app.
    assert "handle /apps/aa/* {" not in full


def test_the_gateway_learns_the_origin_and_a_login_goes_through_wips_origin() -> None:
    full, _, _ = _sub()
    assert "header_up X-Forwarded-Host {http.request.hostport}" in full
    assert (
        "redir * https://wip.example:8443/auth/app-login?app=aa"
        "&return_to={http.request.uri_escaped} 302"
    ) in full
    assert "/auth/login?" not in full


def test_the_app_session_cookie_never_reaches_the_app() -> None:
    full, _, _ = _sub()
    assert 'request_header Cookie "(^|;\\s*)wip_app_session=[^;]*" ""' in full
    assert 'request_header Cookie "(^|;\\s*)wip_session=[^;]*" ""' in full


def test_ask_confirms_only_active_attachments() -> None:
    full, empty, unavailable = _sub()
    assert "\t@t0_ask {\n\t\tpath /edge/ask\n\t\tquery domain=aa.wip.example\n\t}" in full
    assert "\thandle @t0_ask {\n\t\trespond 200\n\t}" in full
    for config in (full, empty, unavailable):
        assert "\thandle /edge/ask {\n\t\trespond 404\n\t}" in config
    assert "respond 200" not in empty and "respond 200" not in unavailable


def test_path_mode_is_unchanged() -> None:
    full = es.self_test_configs()["full"]
    assert "handle /apps/aa/* {" in full
    assert "/edge/ask" not in full and "app-login" not in full
    assert "header_up X-Forwarded-Host wip.example:8443" in full


def test_the_mode_comes_from_the_environment(monkeypatch) -> None:
    for name, value in {
        "PUBLIC_BASE_URL": "https://wip.example:8443", "GATEWAY_URL": "http://wip-auth-gateway:4180",
        "REGISTRY_URL": "http://wip-registry:8001", "EDGE_API_KEY": "k",
        "GATEWAY_IDENTITY_HEADERS": "X-WIP-User", "CLIENT_IDENTITY_HEADERS": "X-WIP-User",
        "WIP_APP_ORIGINS": "subdomain",
    }.items():
        monkeypatch.setenv(name, value)
    s = es.Settings.from_env()
    assert s.app_origins == "subdomain" and s.public_hostname == "wip.example"
    assert dataclasses.replace(s, app_origins="path").app_origins == "path"
