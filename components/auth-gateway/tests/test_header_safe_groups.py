"""Group names that cannot travel in X-WIP-Groups unchanged are dropped.

The gateway joins a session's groups with "," into X-WIP-Groups, and every
consumer splits that header on "," and strips whitespace. The reserved-group
filter compares whole names, so a provider's group "staff,wip-admins" or
" wip-admins" passed it and arrived downstream as "wip-admins". A name outside
latin-1 could not be put in the header at all, and made every /auth/verify
for that user a 500. Such names are dropped at login, for every user; the
login keeps the user's other groups. (CASE-1081.)
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from auth_gateway import oidc
from auth_gateway.main import app
from auth_gateway.oidc import header_safe_groups

from .test_federation_parity import POLICY_JSON, _exchange, dex_sub

UNSAFE = ["staff,wip-admins", "x, wip-services", " wip-admins", "wip-admins ", "Łódź-team", "研究", "a\nb", ""]


def _downstream(groups: list[str]) -> list[str]:
    # What TrustedHeaderProvider (libs/wip-auth) reads back from the header.
    header = ",".join(groups)
    return [g.strip() for g in header.split(",") if g.strip()]


@pytest.mark.parametrize("name", UNSAFE)
def test_a_name_the_header_cannot_carry_is_dropped(name):
    assert header_safe_groups(["devs", name], "u@x.org") == ["devs"]


@pytest.mark.parametrize("name", ["devs", "Zürich-team", "org:team", "a b"])
def test_ordinary_names_are_kept(name):
    assert header_safe_groups([name], "u@x.org") == [name]


def test_a_non_list_claim_gives_no_groups():
    assert header_safe_groups("wip-admins", "u@x.org") == []
    assert header_safe_groups(None, "u@x.org") == []


@pytest.mark.parametrize("name", ["staff,wip-admins", "x, wip-services", " wip-admins"])
def test_a_federated_login_cannot_reach_a_reserved_group_through_the_header(monkeypatch, name):
    claims = {"sub": dex_sub("9", "github"), "email": "bob@corp.com", "groups": ["devs", name]}
    groups = _exchange(monkeypatch, claims, POLICY_JSON)["groups"]
    downstream = _downstream(groups)
    assert "wip-admins" not in downstream and "wip-services" not in downstream
    assert "devs" in downstream


def test_a_local_login_is_held_to_the_same_rule(monkeypatch):
    claims = {"sub": dex_sub("admin-001", "local"), "email": "admin@wip.local", "groups": ["wip-admins", "研究"]}
    assert _exchange(monkeypatch, claims, POLICY_JSON)["groups"] == ["wip-admins"]


def test_a_login_with_a_non_latin1_group_still_verifies(monkeypatch):
    claims = {"sub": dex_sub("9", "github"), "email": "bob@corp.com", "groups": ["Łódź-team", "devs"]}
    user = _exchange(monkeypatch, claims, "")

    async def fake_exchange(request):
        return user

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    client = TestClient(app)
    assert client.get("/auth/callback", params={"code": "c", "state": "s"}, follow_redirects=False).status_code == 302
    r = client.get("/auth/verify")
    assert r.status_code == 200
    assert r.headers["x-wip-groups"] == "devs"


@pytest.mark.parametrize("email", ["łukasz@x.org", "a\r\nX-WIP-Groups: wip-admins@x.org"])
def test_an_email_the_header_cannot_carry_is_refused_at_login(monkeypatch, email):
    async def fake_exchange(request):
        return {"email": email, "groups": [], "sub": "u"}

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    r = TestClient(app).get("/auth/callback", params={"code": "c", "state": "s"}, follow_redirects=False)
    assert r.status_code == 403
    assert "identity header" in r.json()["detail"]
