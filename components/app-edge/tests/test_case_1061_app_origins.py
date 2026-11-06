"""Attached apps on origins of their own (CASE-1061), served at the root
(CASE-1103).

wip-caddy hands every <name>.<hostname> that no deployer app owns to the
edge. The edge then:

- matches each attachment by host, and sends every path on that host to its
  upstream as the browser sent it: no prefix, no strip, no redirect;
- tells the gateway which origin the browser addressed (X-Forwarded-Host),
  and sends a login to the handshake on WIP's origin;
- removes the app session cookie, as it removes wip_session;
- answers wip-caddy's on-demand TLS question (/edge/ask) with 200 only for an
  active attachment's host.
"""

from __future__ import annotations

import edge_sync as es


def _configs() -> tuple[str, str, str]:
    configs = es.self_test_configs()
    return configs["full"], configs["empty"], configs["unavailable"]


def test_each_attachment_is_matched_by_host_alone() -> None:
    full, _, _ = _configs()
    assert "\t@t0 host aa.wip.example\n" in full
    assert "\thandle @t0 {\n" in full
    # Every path on the origin goes through unchanged.
    assert "/apps" not in full
    assert "strip_prefix" not in full


def test_the_gateway_learns_the_origin_and_a_login_goes_through_wips_origin() -> None:
    full, _, _ = _configs()
    assert "header_up X-Forwarded-Host {http.request.hostport}" in full
    assert (
        "redir * https://wip.example:8443/auth/app-login?app=aa"
        "&return_to={http.request.uri_escaped} 302"
    ) in full
    assert "/auth/login?" not in full


def test_the_app_session_cookie_never_reaches_the_app() -> None:
    full, _, _ = _configs()
    assert 'request_header Cookie "(^|;\\s*)wip_app_session=[^;]*" ""' in full
    assert 'request_header Cookie "(^|;\\s*)wip_session=[^;]*" ""' in full


def test_ask_confirms_only_active_attachments() -> None:
    full, empty, unavailable = _configs()
    assert "\t@t0_ask {\n\t\tpath /edge/ask\n\t\tquery domain=aa.wip.example\n\t}" in full
    assert "\thandle @t0_ask {\n\t\trespond 200\n\t}" in full
    for config in (full, empty, unavailable):
        assert "\thandle /edge/ask {\n\t\trespond 404\n\t}" in config
    assert "respond 200" not in empty and "respond 200" not in unavailable


def test_settings_come_from_the_environment(monkeypatch) -> None:
    for name, value in {
        "PUBLIC_BASE_URL": "https://wip.example:8443", "GATEWAY_URL": "http://wip-auth-gateway:4180",
        "REGISTRY_URL": "http://wip-registry:8001", "EDGE_API_KEY": "k",
        "GATEWAY_IDENTITY_HEADERS": "X-WIP-User", "CLIENT_IDENTITY_HEADERS": "X-WIP-User",
    }.items():
        monkeypatch.setenv(name, value)
    s = es.Settings.from_env()
    assert s.public_hostname == "wip.example"
    assert not hasattr(s, "app_origins")
