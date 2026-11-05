"""The app edge's sync loop (CASE-1052).

The edge serves the Registry's active attachments. These tests pin the rules
of the generated config: the dialled address is the checked one, identity
headers follow the deployer's one rule, the gateway's session cookie never
reaches an app, the scheme and host are set rather than trusted, and the
routes expire a fixed time after the last successful read.
"""

from __future__ import annotations

import ipaddress
import json

import pytest

import edge_sync as es

DNS = {
    "aa.apps.example": {"203.0.113.10", "2001:db8::10"},
    "wip-registry": {"10.89.0.5"},
    "alias-of-registry.example": {"10.89.0.5"},
    "rebind.example": {"127.0.0.1"},
    "meta.example": {"169.254.169.254"},
    "nat64.example": {"64:ff9b::7f00:1"},
}


@pytest.fixture(autouse=True)
def both_families(monkeypatch):
    import socket
    monkeypatch.setattr(es, "reachable_families", lambda: {socket.AF_INET, socket.AF_INET6})


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    def resolve(host):
        try:
            return {ipaddress.ip_address(host)}
        except ValueError:
            return {ipaddress.ip_address(a) for a in DNS.get(host, set())}

    monkeypatch.setattr(es, "resolve", resolve)


SETTINGS = es.Settings(
    registry_url="http://wip-registry:8001",
    api_key="k",
    gateway="wip-auth-gateway:4180",
    public_scheme="https",
    public_host="wip.example:8443",
    gateway_headers=("X-WIP-User", "X-WIP-Groups"),
    client_headers=("X-WIP-User", "X-WIP-Groups", "X-WIP-Auth-Method"),
    policy_file=None,
)


def rec(name="aa", upstream="https://aa.apps.example", **extra):
    return {"name": name, "upstream": upstream, "auth_required": True, "allowed_groups": [], **extra}


def block(cfg: str, matcher: str) -> str:
    start = cfg.index(f"\thandle {matcher} {{\n")
    return cfg[start:cfg.index("\n\t}\n", start)]


# ── plan: resolution and the blocklist ────────────────────────────────────


def test_the_dialled_addresses_are_the_checked_ones():
    p = es.plan([rec()], ["wip-registry"])
    assert [t.record["name"] for t in p.targets] == ["aa"]
    cfg = es.caddyfile(p, SETTINGS)
    body = block(cfg, "/apps/aa/*")
    assert "reverse_proxy [2001:db8::10]:443 203.0.113.10:443 {" in body
    assert "tls_server_name aa.apps.example" in body
    assert "aa.apps.example:443" not in body  # the name is never dialled


@pytest.mark.parametrize(
    ("upstream", "why"),
    [
        ("http://wip-registry:8001", "install's own hosts"),
        ("http://alias-of-registry.example", "install's own host wip-registry"),
        ("http://10.89.0.5:8001", "install's own host wip-registry"),
        ("http://rebind.example", "loopback"),
        ("http://meta.example", "link-local"),
        ("http://nat64.example", "loopback"),
        ("http://[fd00:ec2::254]", "cloud metadata"),
        ("http://100.100.100.200", "cloud metadata"),
        ("http://unknown.example", "does not resolve"),
        ("http://aa.apps.example/base", "a path"),
    ],
)
def test_unservable_upstreams_are_skipped(upstream, why):
    p = es.plan([rec(upstream=upstream)], ["wip-registry"])
    assert p.targets == []
    assert why in p.skipped["aa"]


def test_one_blocked_address_among_several_skips_the_record(monkeypatch):
    DNS["mixed.example"] = {"203.0.113.11", "127.0.0.1"}
    try:
        p = es.plan([rec(upstream="https://mixed.example")], [])
        assert p.targets == [] and "loopback" in p.skipped["aa"]
    finally:
        del DNS["mixed.example"]


def test_records_the_edge_cannot_quote_safely_are_skipped():
    p = es.plan([
        rec(name="bad name"), rec(name="bb", allowed_groups=["a b"]), rec(name="cc", health="/x;y"),
        rec(name="dd", strip_prefix=True, preserve_prefix_subpaths=["/apps/other/api"]),
        rec(name="ee", strip_prefix=True, preserve_prefix_subpaths=["/apps/ee/../other"]),
    ], [])
    assert set(p.skipped) == {"bad name", "bb", "cc", "dd", "ee"}
    assert "outside /apps/dd/" in p.skipped["dd"]


def test_policy_file_fails_closed(tmp_path):
    assert es.load_blocked_hosts(None) == []
    with pytest.raises(es.PolicyUnavailable):
        es.load_blocked_hosts(str(tmp_path / "missing.json"))
    empty = tmp_path / "empty.json"
    empty.write_text("")
    with pytest.raises(es.PolicyUnavailable):
        es.load_blocked_hosts(str(empty))
    ok = tmp_path / "p.json"
    ok.write_text('{"blocked_hosts": ["WIP-Registry."], "reserved_names": []}')
    assert es.load_blocked_hosts(str(ok)) == ["wip-registry"]


# ── the generated routes ──────────────────────────────────────────────────


def test_protected_route_order_and_headers():
    cfg = es.caddyfile(es.plan([rec(allowed_groups=["aa-users"])], []), SETTINGS)
    body = block(cfg, "/apps/aa/*")
    order = [
        "forward_auth wip-auth-gateway:4180 {",
        "uri /auth/verify?allowed_groups=aa-users",
        "header_up X-Forwarded-Proto https",
        "header_up X-Forwarded-Host wip.example:8443",
        "copy_headers X-WIP-User X-WIP-Groups",
        "request_header -X-WIP-Auth-Method",
        'request_header Cookie "(^|;\\s*)wip_session=[^;]*" ""',
        'request_header Cookie "^;\\s*" ""',
        "reverse_proxy ",
    ]
    positions = [body.index(s) for s in order]
    assert positions == sorted(positions)
    assert "\t\troute {" in body  # written order is execution order
    # The gateway's own headers are never removed after its copy.
    assert "request_header -X-WIP-User" not in body
    assert "request_header -X-WIP-Groups" not in body


def test_unprotected_route_strips_every_client_identity_header():
    cfg = es.caddyfile(es.plan([rec(auth_required=False)], []), SETTINGS)
    body = block(cfg, "/apps/aa/*")
    assert "forward_auth" not in body
    for h in SETTINGS.client_headers:
        assert f"request_header -{h}\n" in body
    assert "wip_session" in body


def test_forwarded_headers_are_set_toward_the_app_too():
    body = block(es.caddyfile(es.plan([rec()], []), SETTINGS), "/apps/aa/*")
    proxy = body[body.index("reverse_proxy"):]
    assert "header_up X-Forwarded-Proto https" in proxy
    assert "header_up X-Forwarded-Host wip.example:8443" in proxy


def test_bare_path_redirect_and_strip_prefix():
    cfg = es.caddyfile(es.plan([rec(strip_prefix=True, preserve_prefix_subpaths=["/apps/aa/health"])], []), SETTINGS)
    assert "\thandle /apps/aa {\n\t\tredir * /apps/aa/ permanent" in cfg
    main = block(cfg, "/apps/aa/*")
    assert main.index("forward_auth") < main.index("uri strip_prefix /apps/aa") < main.index("reverse_proxy")
    assert "strip_prefix" not in block(cfg, "/apps/aa/health/*")


def test_proxy_options():
    pem = "-----BEGIN CERTIFICATE-----\nAAEC\nAwQ=\n-----END CERTIFICATE-----\n"  # syntax only
    cfg = es.caddyfile(es.plan([rec(
        upstream="https://aa.apps.example:9443", upstream_host_header=True, streaming=True,
        health="/health", upstream_tls={"verify": True, "ca_pem": pem},
    )], []), SETTINGS)
    body = block(cfg, "/apps/aa/*")
    for line in [
        "header_up Host aa.apps.example:9443",
        "flush_interval -1",
        "health_uri /health",
        "tls_trust_pool inline {",
        "trust_der AAECAwQ=",
    ]:
        assert line in body


def test_tls_verify_off_and_plain_http():
    off = block(es.caddyfile(es.plan([rec(upstream_tls={"verify": False})], []), SETTINGS), "/apps/aa/*")
    assert "tls_insecure_skip_verify" in off
    plain = block(es.caddyfile(es.plan([rec(upstream="http://aa.apps.example:3000")], []), SETTINGS), "/apps/aa/*")
    # A plain-http upstream gets the dial timeout, but no TLS.
    assert "dial_timeout 2s" in plain and "\t\t\t\t\ttls\n" not in plain
    assert "203.0.113.10:3000" in plain


def test_the_front_doors_forwarded_for_is_kept():
    # Without trust Caddy replaces X-Forwarded-For with its peer, wip-caddy,
    # and every attached app sees the front door as its only client.
    trust = "\tservers {\n\t\ttrusted_proxies static private_ranges\n\t}\n}\n"
    for cfg in (es.caddyfile(es.plan([rec()], []), SETTINGS), es.caddyfile(None, SETTINGS)):
        assert cfg.startswith("{\n") and trust in cfg[: cfg.index(f":{es.LISTEN_PORT} {{")]


def test_unknown_paths_404_and_no_plan_503():
    cfg = es.caddyfile(es.plan([], []), SETTINGS)
    assert 'respond "No app is attached at this path" 404' in cfg
    down = es.caddyfile(None, SETTINGS, unavailable="the Registry is not answering")
    assert '"Attached apps are unavailable: the Registry is not answering" 503' in down


# ── expiry ────────────────────────────────────────────────────────────────


def test_routes_expire_after_three_failed_polls(monkeypatch):
    calls = {"ok": True}

    def fetch(_s):
        if calls["ok"]:
            return [rec()]
        raise es.RegistryUnavailable("down")

    monkeypatch.setattr(es, "fetch_attachments", fetch)
    state = es.State()
    good = es.tick(SETTINGS, state, now=100.0)
    state.loaded = good
    assert "/apps/aa/*" in good

    calls["ok"] = False
    # Within 3 * POLL_SECONDS of the last success: the loaded config stays.
    assert es.tick(SETTINGS, state, now=100.0 + 2 * es.POLL_SECONDS) == good
    # A failed poll never extends the deadline.
    assert es.tick(SETTINGS, state, now=100.0 + 2.9 * es.POLL_SECONDS) == good
    expired = es.tick(SETTINGS, state, now=100.0 + 3.1 * es.POLL_SECONDS)
    assert "the Registry is not answering" in expired and "/apps/aa/*" not in expired


def test_never_read_means_unavailable(monkeypatch):
    monkeypatch.setattr(es, "fetch_attachments", lambda _s: (_ for _ in ()).throw(es.RegistryUnavailable("x")))
    assert "503" in es.tick(SETTINGS, es.State(), now=1.0)


def test_header_lists_must_be_header_names():
    with pytest.raises(ValueError):
        es._header_list("X-WIP-User, bad header")


def test_the_fallback_drops_only_records_with_their_own_ca():
    pem = "-----BEGIN CERTIFICATE-----\nAAEC\n-----END CERTIFICATE-----\n"
    p = es.plan([rec(upstream_tls={"verify": True, "ca_pem": pem}), rec(name="bb")], [])
    fb = es.without_custom_cas(p)
    assert [t.record["name"] for t in fb.targets] == ["bb"]
    assert "CA certificate" in fb.skipped["aa"]


def test_a_skipped_record_is_reported_once(monkeypatch, caplog):
    monkeypatch.setattr(es, "fetch_attachments", lambda _s: [rec(upstream="http://127.0.0.1")])
    state = es.State()
    with caplog.at_level("WARNING", logger="app-edge"):
        es.tick(SETTINGS, state, now=1.0)
        es.tick(SETTINGS, state, now=2.0)
    assert sum("not served" in r.message for r in caplog.records) == 1


def test_unreachable_families_are_not_dialled():
    import socket
    p = es.plan([rec()], [], families={socket.AF_INET})
    body = block(es.caddyfile(p, SETTINGS), "/apps/aa/*")
    assert "reverse_proxy 203.0.113.10:443 {" in body and "2001:db8" not in body
    v6only = es.plan([rec(upstream="https://[2001:db8::20]")], [], families={socket.AF_INET})
    assert v6only.targets == [] and "no route" in v6only.skipped["aa"]


def test_a_failed_dial_moves_to_the_next_address():
    body = block(es.caddyfile(es.plan([rec()], []), SETTINGS), "/apps/aa/*")
    for line in ["lb_try_duration 10s", "lb_try_interval 250ms", "fail_duration 30s", "max_fails 1", "dial_timeout 2s"]:
        assert line in body, line


def test_a_refused_config_never_leaves_the_old_one_serving(monkeypatch):
    loads = []

    def load(config, timeout=10.0):
        loads.append(config)
        if "Attached apps are unavailable" not in config:
            raise OSError("refused")

    monkeypatch.setattr(es, "load", load)
    state = es.State(loaded="OLD", plan=es.plan([rec()], []))
    now_serving = es.apply("NEW", state, SETTINGS)
    assert "the app edge's config was refused" in now_serving
    assert len(loads) == 3  # the config, the CA-free fallback, the unavailable one


def test_a_refused_key_is_named_as_such(monkeypatch):
    import io
    import urllib.error

    def refuse(*a, **k):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b""))

    monkeypatch.setattr(es.urllib.request, "urlopen", refuse)
    cfg = es.tick(SETTINGS, es.State(), now=1.0)
    assert "the Registry refused the app edge's key" in cfg


def test_the_self_test_covers_every_option():
    configs = es.self_test_configs()
    full = configs["full"]
    for needle in ["tls_trust_pool inline", "trust_der ", "tls_insecure_skip_verify", "uri strip_prefix",
                   "flush_interval -1", "health_uri", "lb_try_duration", "allowed_groups=aa-users",
                   'request_header Cookie "(^|;\\s*)wip_session']:
        assert needle in full, needle
    assert "unavailable" in configs["unavailable"]


# ── the cluster's node ranges (CASE-1062) ──────────────────────────────────


def test_an_upstream_in_a_node_range_is_skipped():
    nodes = [ipaddress.ip_network("192.168.1.20/32")]
    p = es.plan([rec(name="api", upstream="https://192.168.1.20:6443"),
                 rec(name="aa", upstream="http://192.168.1.30:30080")], [], blocked_cidrs=nodes)
    assert "node range" in p.skipped["api"]
    assert [t.record["name"] for t in p.targets] == ["aa"]


def test_the_policy_carries_the_node_ranges(tmp_path):
    f = tmp_path / "policy.json"
    f.write_text(json.dumps({"blocked_hosts": ["wip-registry"], "blocked_cidrs": ["192.168.1.20/32"]}))
    hosts, cidrs = es.load_policy(str(f))
    assert hosts == ["wip-registry"] and cidrs == [ipaddress.ip_network("192.168.1.20/32")]
    f.write_text(json.dumps({"blocked_hosts": ["wip-registry"]}))
    assert es.load_policy(str(f))[1] == []


def test_a_bad_node_range_fails_closed(tmp_path):
    f = tmp_path / "policy.json"
    f.write_text(json.dumps({"blocked_hosts": [], "blocked_cidrs": ["nope"]}))
    with pytest.raises(es.PolicyUnavailable):
        es.load_policy(str(f))
