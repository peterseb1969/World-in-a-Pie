"""Login's return_to must never send the browser off-site.

`/auth/login?return_to=…` is where a user lands after a genuine login on the
real WIP host, so an unchecked value is a phishing tool: the link starts at
WIP and ends on any site the attacker names, at the moment the user trusts
it most. Login accepts what logout accepts (a same-origin path) plus the
absolute URL on this install's own host that the gateway's redirect mode
builds itself; anything else becomes the default redirect. (CASE-1080.)
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from auth_gateway import oidc
from auth_gateway.config import settings
from auth_gateway.main import _safe_return_to, _same_origin_path, app


@pytest.mark.parametrize(
    "value",
    [
        "/apps/kb/",
        "/apps/kb/search?q=foo&page=3",
        "https://wip.local/apps/kb/",
        "https://wip.local:8443/apps/kb/?a=b",
        "http://WIP.local/apps/rc/",
    ],
)
def test_same_install_targets_are_kept(value):
    assert _safe_return_to(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.example/",
        "//evil.example/x",
        "/\\evil.example",
        "https://wip.local@evil.example/",
        "https://user@wip.local/apps/kb/",
        "https://wip.local.evil.example/",
        "javascript:alert(1)",
        "ftp://wip.local/x",
        "/\t/evil.example",
        "/\n/evil.example",
        "https://wip.local\t/x",
        "https://[::1",
        "",
    ],
)
def test_anything_else_becomes_the_default(value):
    assert _safe_return_to(value) == settings.default_redirect


def test_logout_refuses_a_path_that_a_browser_turns_off_site():
    # Browsers delete tabs and newlines, so these arrive as "//evil.example".
    assert not _same_origin_path("/\t/evil.example")
    assert not _same_origin_path("/\r\n/evil.example")


@pytest.fixture
def login_flow(monkeypatch):
    async def fake_auth_url(request):
        return "https://wip.local/dex/auth?state=x"

    async def fake_exchange(request):
        return {"email": "u@wip.local", "groups": [], "sub": "u"}

    monkeypatch.setattr(oidc, "get_auth_url", fake_auth_url)
    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)

    def run(return_to: str | None) -> str:
        client = TestClient(app)
        params = {} if return_to is None else {"return_to": return_to}
        r = client.get("/auth/login", params=params, follow_redirects=False)
        assert r.status_code in (302, 307), r.text
        r = client.get("/auth/callback", params={"code": "c", "state": "x"}, follow_redirects=False)
        assert r.status_code == 302, r.text
        return r.headers["location"]

    return run


@pytest.mark.parametrize("value", ["https://evil.example/", "//evil.example/x", "/\t/evil.example"])
def test_a_login_never_ends_off_site(login_flow, value):
    assert login_flow(value) == settings.default_redirect


@pytest.mark.parametrize(
    "value", ["/apps/kb/search?q=foo&page=3", "https://wip.local/apps/kb/"]
)
def test_a_login_returns_to_where_it_started(login_flow, value):
    assert login_flow(value) == value


def test_a_login_without_return_to_lands_on_the_default(login_flow):
    assert login_flow(None) == settings.default_redirect
