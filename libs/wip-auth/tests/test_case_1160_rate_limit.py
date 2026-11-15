"""Rate limiting is enforced per client on requests through the proxy, and not
on the platform's own calls.

setup_rate_limiting configured a slowapi limiter but never installed the
middleware that applies its default limit, so no request was ever limited.
It now installs one that limits each request carrying X-Forwarded-For (what
Caddy writes for every request it forwards) per client address, before
authentication; a request without the header is a call from inside the
platform and passes, and lifecycle probes are never limited. The app here is
wired exactly as the services wire it: setup_auth, then setup_rate_limiting,
then CORS.
"""

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from wip_auth import (
    AuthConfig,
    UserIdentity,
    require_identity,
    reset_auth_config,
    set_auth_config,
    setup_auth,
)
from wip_auth.ratelimit import client_address, setup_rate_limiting, trusted_proxies

KEY = {"X-API-Key": "test_key_1160"}


def _client(xff: str | None) -> dict:
    return {"X-Forwarded-For": xff} if xff else {}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("WIP_TRUSTED_PROXIES", raising=False)
    yield from _limited_app(monkeypatch)


@pytest.fixture
def client_behind_ingress(monkeypatch):
    """The service as the k8s target renders it: wip-caddy trusts the pod
    ranges, and so does the limit."""
    monkeypatch.setenv("WIP_TRUSTED_PROXIES", "10.42.0.0/16")
    yield from _limited_app(monkeypatch)


def _limited_app(monkeypatch):
    monkeypatch.setenv("WIP_RATE_LIMIT", "3/minute")
    config = AuthConfig(mode="api_key_only", legacy_api_key="test_key_1160")
    set_auth_config(config)
    app = FastAPI()
    setup_auth(app, config, public_paths=["/api/svc/health"])
    setup_rate_limiting(app)
    app.add_middleware(
        CORSMiddleware, allow_origins=["https://ui.example"], allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routes in an included router, as every service mounts its API: FastAPI
    # 0.162 keeps them behind one _IncludedRouter entry, which slowapi's own
    # route lookup cannot see into (it then exempts the request).
    router = APIRouter()

    @router.get("/api/svc/thing")
    async def thing(identity: UserIdentity = Depends(require_identity())):
        return {"user": identity.username}

    @router.get("/api/svc/things/{thing_id}")
    async def one_thing(thing_id: str, identity: UserIdentity = Depends(require_identity())):
        return {"id": thing_id}

    @router.get("/api/svc/health")
    async def health():
        return {"status": "healthy"}

    app.include_router(router)
    yield TestClient(app)
    reset_auth_config()


def test_a_client_past_the_limit_gets_429_with_its_retry_window(client):
    for _ in range(3):
        assert client.get("/api/svc/thing", headers={**KEY, **_client("203.0.113.7")}).status_code == 200
    refused = client.get("/api/svc/thing", headers={**KEY, **_client("203.0.113.7")})
    assert refused.status_code == 429, refused.text
    assert "Rate limit exceeded" in refused.json()["detail"]
    assert int(refused.headers["Retry-After"]) > 0
    assert refused.headers["X-RateLimit-Limit"] == "3"


def test_one_budget_covers_every_path_of_the_service(client):
    # Not a budget per URL: then every distinct path, each document id among
    # them, would start a fresh budget and the limit would bind nobody.
    xff = _client("203.0.113.15")
    for path in ("/api/svc/thing", "/api/svc/things/a", "/api/svc/things/b"):
        assert client.get(path, headers={**KEY, **xff}).status_code == 200
    assert client.get("/api/svc/things/c", headers={**KEY, **xff}).status_code == 429


def test_every_response_through_the_proxy_reports_its_budget(client):
    first = client.get("/api/svc/thing", headers={**KEY, **_client("203.0.113.14")})
    assert (first.headers["X-RateLimit-Limit"], first.headers["X-RateLimit-Remaining"]) == ("3", "2")
    internal = client.get("/api/svc/thing", headers=KEY)
    assert "X-RateLimit-Limit" not in internal.headers


def test_each_client_address_has_its_own_bucket(client):
    for _ in range(3):
        client.get("/api/svc/thing", headers={**KEY, **_client("203.0.113.8")})
    assert client.get("/api/svc/thing", headers={**KEY, **_client("203.0.113.9")}).status_code == 200


def test_behind_trusted_proxies_the_client_is_the_rightmost_untrusted_address(
    client_behind_ingress,
):
    # On k8s Caddy keeps the ingress controller's header and appends its peer,
    # a pod address: which controller pod forwarded does not change the client.
    for _ in range(3):
        client_behind_ingress.get(
            "/api/svc/thing", headers={**KEY, **_client("203.0.113.10, 10.42.0.5")},
        )
    other_proxy = _client("203.0.113.10, 10.42.0.6")
    assert client_behind_ingress.get(
        "/api/svc/thing", headers={**KEY, **other_proxy},
    ).status_code == 429


def test_an_address_the_client_sends_does_not_buy_it_a_new_bucket(client_behind_ingress):
    # The controller appends the address it saw; whatever the client put in
    # the header sits left of it. Keyed on the leftmost entry, every request
    # with a fresh made-up address started a fresh budget.
    for n in range(3):
        client_behind_ingress.get("/api/svc/thing", headers={
            **KEY, **_client(f"198.51.100.{n}, 203.0.113.20, 10.42.0.5"),
        })
    spoofed = _client("198.51.100.99, 203.0.113.20, 10.42.0.5")
    assert client_behind_ingress.get(
        "/api/svc/thing", headers={**KEY, **spoofed},
    ).status_code == 429


def test_a_header_of_trusted_addresses_only_is_keyed_on_its_leftmost(client_behind_ingress):
    # An app pod calling through wip-router: the router writes the app's own
    # (pod) address, and the app is one client.
    for _ in range(3):
        client_behind_ingress.get("/api/svc/thing", headers={**KEY, **_client("10.42.1.7")})
    assert client_behind_ingress.get(
        "/api/svc/thing", headers={**KEY, **_client("10.42.1.8")},
    ).status_code == 200
    assert client_behind_ingress.get(
        "/api/svc/thing", headers={**KEY, **_client("10.42.1.7")},
    ).status_code == 429


@pytest.mark.parametrize(("forwarded", "trusted", "expected"), [
    ("203.0.113.1", "", "203.0.113.1"),
    ("198.51.100.1, 203.0.113.1", "", "203.0.113.1"),
    ("198.51.100.1, 203.0.113.1, 10.42.0.5", "10.42.0.0/16", "203.0.113.1"),
    ("10.42.0.9, 10.42.0.5", "10.42.0.0/16", "10.42.0.9"),
    ("2001:db8::1, fd00::5", "fd00::/56", "2001:db8::1"),
    ("not-an-address, 10.42.0.5", "10.42.0.0/16", "not-an-address"),
    (" 203.0.113.1 ,, 10.42.0.5 ", "10.42.0.0/16", "203.0.113.1"),
])
def test_client_address(monkeypatch, forwarded, trusted, expected):
    monkeypatch.setenv("WIP_TRUSTED_PROXIES", trusted)
    assert client_address(forwarded, trusted_proxies()) == expected


@pytest.mark.parametrize("limit", ["3/minute", ""], ids=["limit-on", "limit-off"])
def test_a_trusted_range_that_is_not_one_fails_the_start(monkeypatch, limit):
    # Off as well as on: a broken setting is found at start, not the day
    # someone turns the limit on.
    monkeypatch.setenv("WIP_RATE_LIMIT", limit)
    monkeypatch.setenv("WIP_TRUSTED_PROXIES", "10.42.0.0/16,pods")
    with pytest.raises(ValueError, match="'pods'"):
        setup_rate_limiting(FastAPI())


def test_internal_calls_are_not_limited(client):
    for _ in range(10):
        assert client.get("/api/svc/thing", headers=KEY).status_code == 200


def test_probes_are_not_limited(client):
    for _ in range(10):
        assert client.get("/api/svc/health", headers=_client("203.0.113.11")).status_code == 200


def test_the_limit_runs_before_authentication(client):
    # A flood of wrong keys is shed without checking each key.
    bad = {"X-API-Key": "wrong", **_client("203.0.113.12")}
    assert [client.get("/api/svc/thing", headers=bad).status_code for _ in range(4)] == [
        401, 401, 401, 429,
    ]


def test_preflight_does_not_spend_budget(client):
    preflight = {
        "Origin": "https://ui.example", "Access-Control-Request-Method": "GET",
        **_client("203.0.113.13"),
    }
    for _ in range(5):
        assert client.options("/api/svc/thing", headers=preflight).status_code == 200
    assert client.get("/api/svc/thing", headers={**KEY, **_client("203.0.113.13")}).status_code == 200


def test_an_empty_setting_disables_it(monkeypatch):
    monkeypatch.setenv("WIP_RATE_LIMIT", "")
    app = FastAPI()
    assert setup_rate_limiting(app) is None
