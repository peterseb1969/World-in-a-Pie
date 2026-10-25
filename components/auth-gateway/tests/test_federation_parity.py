"""The gateway applies the federated group policy, exactly as wip-auth does.

Browser logins reach the services through the gateway's session (route
gates, X-WIP-Groups); bearer tokens through wip-auth's OIDC provider. Both
must give a federated login the same groups, so the gateway's copy of the
policy is held to wip_auth.federation's behaviour on one set of cases, and
the login exchange is shown to apply it before the session is stored.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os

os.environ.setdefault("OIDC_ISSUER", "https://wip.local/dex")
os.environ.setdefault("OIDC_INTERNAL_ISSUER", "http://wip-dex:5556/dex")
os.environ.setdefault("OIDC_CLIENT_ID", "wip-gateway")
os.environ.setdefault("OIDC_CLIENT_SECRET", "test-secret")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("WIP_HOSTNAME", "wip.local")
os.environ.setdefault("CALLBACK_URL", "https://wip.local/auth/callback")

import pytest

from auth_gateway import federation as gateway
from auth_gateway import oidc
from wip_auth import federation as library


def dex_sub(user_id: str, connector: str) -> str:
    raw = (b"\n" + bytes([len(user_id)]) + user_id.encode()
           + b"\x12" + bytes([len(connector)]) + connector.encode())
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


POLICY_JSON = json.dumps({
    "local_connectors": ["local"],
    "reserved_groups": ["wip-admins", "wip-services"],
    "mappings": [
        {"email_domain": "corp.com", "group": "wip-editors"},
        {"email": "ana@else.org", "group": "wip-viewers"},
    ],
})

CASES = [
    ({"sub": dex_sub("admin-001", "local")}, ["wip-admins"]),
    ({"sub": dex_sub("9", "google"), "email": "bob@corp.com"}, ["wip-admins", "devs"]),
    ({"sub": dex_sub("9", "google"), "email": "Ana@Else.org"}, ["wip-services"]),
    ({"sub": dex_sub("9", "google"), "email": "bob@corp.com", "email_verified": False}, []),
    ({"sub": "opaque-non-dex", "email": "x@corp.com"}, ["wip-admins"]),
    ({"sub": dex_sub("9", "local"), "federated_claims": {"connector_id": "github"}}, ["wip-admins"]),
    ({"sub": ""}, ["team"]),
]


@pytest.mark.parametrize(("claims", "groups"), CASES)
def test_gateway_copy_matches_wip_auth(claims: dict, groups: list[str]) -> None:
    assert gateway.connector_id(claims) == library.connector_id(claims)
    ours = gateway.FederatedGroupPolicy.from_json(POLICY_JSON)
    theirs = library.FederatedGroupPolicy.from_json(POLICY_JSON)
    assert ours is not None and theirs is not None
    assert ours.apply(claims, groups) == theirs.apply(claims, groups)


class _Request:
    def __init__(self) -> None:
        self.query_params = {"code": "c", "state": "s"}
        self.session = {"oauth_state": "s", "code_verifier": "v"}


def _id_token(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{body}.sig"


def _exchange(monkeypatch: pytest.MonkeyPatch, claims: dict, policy: str) -> dict:
    async def discover() -> dict:
        return {"token_endpoint": "http://wip-dex:5556/dex/token"}

    class _Response:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict:
            return {"id_token": _id_token(claims)}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None: ...

        async def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(oidc, "_discover_metadata", discover)
    monkeypatch.setattr(oidc.httpx, "AsyncClient", _Client)
    monkeypatch.setattr(oidc.settings, "federated_groups", policy)
    return asyncio.run(oidc.exchange_code(_Request()))


def test_login_through_a_connector_drops_reserved_groups_and_maps(monkeypatch) -> None:
    claims = {"sub": dex_sub("9", "github"), "email": "bob@corp.com", "groups": ["wip-admins", "devs"]}
    assert _exchange(monkeypatch, claims, POLICY_JSON)["groups"] == ["devs", "wip-editors"]


def test_local_login_keeps_its_groups(monkeypatch) -> None:
    claims = {"sub": dex_sub("admin-001", "local"), "email": "admin@wip.local", "groups": ["wip-admins"]}
    assert _exchange(monkeypatch, claims, POLICY_JSON)["groups"] == ["wip-admins"]


def test_without_a_policy_groups_stay_as_issued(monkeypatch) -> None:
    claims = {"sub": dex_sub("9", "github"), "email": "bob@corp.com", "groups": ["wip-admins"]}
    assert _exchange(monkeypatch, claims, "")["groups"] == ["wip-admins"]
