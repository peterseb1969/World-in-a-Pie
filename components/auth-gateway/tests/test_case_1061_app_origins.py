"""Apps on origins of their own: the gateway's handshake (CASE-1061).

With WIP_APP_ORIGINS=subdomain each app is served at
https://<label>.<hostname>[:port] with a session cookie of its own,
wip_app_session, handed over from WIP's origin with a one-time code. These
tests drive the real routes through Starlette's session middleware: a
wip_session cookie signed the way the middleware signs it, then
/auth/app-login, /auth/handoff and /auth/verify as the proxy calls them
(X-Forwarded-Host naming the origin the browser addressed).
"""

from __future__ import annotations

import json
import time
from base64 import b64encode
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from auth_gateway import app_origins
from auth_gateway.config import settings
from auth_gateway.main import app

WIP = "wip.local:8443"
RC = "rc.wip.local:8443"
AA = "aa.wip.local:8443"


@pytest.fixture(autouse=True)
def public_url(monkeypatch):
    monkeypatch.setattr(settings, "public_url", "https://wip.local:8443")
    monkeypatch.setattr(settings, "app_session_max_age", 900)
    app_origins._used_codes.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _wip_session(**extra) -> str:
    """A wip_session cookie as Starlette's SessionMiddleware signs it."""
    session = {"email": "ann@wip.local", "groups": ["wip-users"], "name": "Ann",
               "user_id": "u1", "exp": int(time.time()) + 3600, **extra}
    data = b64encode(json.dumps(session).encode())
    return TimestampSigner(settings.session_secret).sign(data).decode()


def _get(client, path, host, cookies=None, **headers):
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return client.get(path, headers={"X-Forwarded-Host": host, **headers}, follow_redirects=False)


def _handshake(client, label="rc", return_to="/x?y=1", **session):
    """app-login on WIP's origin, then handoff on the app origin. Returns
    the handoff response and the app session cookie it set."""
    login = _get(client, f"/auth/app-login?app={label}&return_to={return_to}", WIP,
                 {"wip_session": _wip_session(**session)})
    assert login.status_code == 302, login.text
    target = urlsplit(login.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == f"https://{label}.wip.local:8443/auth/handoff"
    handoff = _get(client, f"{target.path}?{target.query}", f"{label}.wip.local:8443")
    return handoff, handoff.cookies.get(app_origins.APP_COOKIE)


def _code_from(location: str) -> str:
    return parse_qs(urlsplit(location).query)["code"][0]


# ── the handshake ─────────────────────────────────────────────────────────


def test_the_handshake_gives_the_app_origin_a_session_of_its_own(client):
    handoff, cookie = _handshake(client)
    assert handoff.status_code == 302
    assert handoff.headers["location"] == "/x?y=1"
    set_cookie = handoff.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "secure" in set_cookie and "samesite=lax" in set_cookie
    assert "domain=" not in set_cookie  # host-only: no other origin receives it

    ok = _get(client, "/auth/verify", RC, {app_origins.APP_COOKIE: cookie})
    assert ok.status_code == 200
    assert ok.headers["X-WIP-User"] == "ann@wip.local"
    assert ok.headers["X-WIP-Groups"] == "wip-users"


def test_an_app_session_is_refused_at_another_app(client):
    _, cookie = _handshake(client, "rc")
    other = _get(client, "/auth/verify", AA, {app_origins.APP_COOKIE: cookie})
    assert other.status_code == 401
    assert other.headers["X-Auth-Redirect"].startswith(
        "https://wip.local:8443/auth/app-login?app=aa&return_to="
    )


def test_the_wip_session_counts_for_nothing_on_an_app_origin(client):
    r = _get(client, "/auth/verify", RC, {"wip_session": _wip_session()})
    assert r.status_code == 401


def test_a_code_is_redeemed_once(client):
    login = _get(client, "/auth/app-login?app=rc", WIP, {"wip_session": _wip_session()})
    path = urlsplit(login.headers["location"])
    first = _get(client, f"{path.path}?{path.query}", RC)
    second = _get(client, f"{path.path}?{path.query}", RC)
    assert first.status_code == 302 and app_origins.APP_COOKIE in first.cookies
    assert second.status_code == 400


def test_a_code_is_redeemed_only_by_its_app(client):
    login = _get(client, "/auth/app-login?app=rc", WIP, {"wip_session": _wip_session()})
    code = _code_from(login.headers["location"])
    assert _get(client, f"/auth/handoff?code={code}", AA).status_code == 400


def test_a_code_expires(client, monkeypatch):
    login = _get(client, "/auth/app-login?app=rc", WIP, {"wip_session": _wip_session()})
    code = _code_from(login.headers["location"])
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + app_origins.CODE_MAX_AGE + 5)
    assert _get(client, f"/auth/handoff?code={code}", RC).status_code == 400


def test_an_app_session_never_outlives_the_wip_session(client):
    handoff, _ = _handshake(client, exp=int(time.time()) + 60)
    max_age = int(handoff.headers["set-cookie"].lower().split("max-age=")[1].split(";")[0])
    assert 0 < max_age <= 60


def test_an_app_session_expires(client, monkeypatch):
    _, cookie = _handshake(client)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 901)
    assert _get(client, "/auth/verify", RC, {app_origins.APP_COOKIE: cookie}).status_code == 401


def test_allowed_groups_hold_on_an_app_origin(client):
    _, cookie = _handshake(client)
    r = _get(client, "/auth/verify?allowed_groups=wip-admins", RC, {app_origins.APP_COOKIE: cookie})
    assert r.status_code == 403


def test_a_return_path_off_the_origin_is_dropped(client):
    handoff, _ = _handshake(client, return_to="//evil.example/x")
    assert handoff.headers["location"] == "/"


# ── not logged in, renew, logout ─────────────────────────────────────────


def test_app_login_without_a_session_logs_in_first(client):
    r = _get(client, "/auth/app-login?app=rc&return_to=/", WIP)
    assert r.status_code == 302
    login = urlsplit(r.headers["location"])
    assert login.path == "/auth/login"
    back = parse_qs(login.query)["return_to"][0]
    assert back.startswith("/auth/app-login?app=rc&return_to=")


def test_a_silent_app_login_without_a_session_reports_back(client):
    r = _get(client, "/auth/app-login?app=rc&return_to=/auth/renewed&silent=1", WIP)
    assert r.headers["location"] == "https://rc.wip.local:8443/auth/renewed?error=login_required"


def test_renew_runs_the_handshake_silently(client):
    r = _get(client, "/auth/renew", RC)
    target = urlsplit(r.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == "https://wip.local:8443/auth/app-login"
    query = parse_qs(target.query)
    assert query["app"] == ["rc"] and query["silent"] == ["1"]
    assert query["return_to"] == [app_origins.RENEWED_PATH]


def test_renewed_reports_the_fresh_session(client):
    _, cookie = _handshake(client, return_to=app_origins.RENEWED_PATH)
    page = _get(client, app_origins.RENEWED_PATH, RC, {app_origins.APP_COOKIE: cookie}).text
    assert '"ok": true' in page and '"expires_in": ' in page
    failed = _get(client, f"{app_origins.RENEWED_PATH}?error=login_required", RC).text
    assert '"ok": false' in failed and "login_required" in failed


def test_logout_on_an_app_origin_ends_the_login_on_wips_origin(client):
    _, cookie = _handshake(client)
    r = _get(client, "/auth/logout", RC, {app_origins.APP_COOKIE: cookie})
    assert r.headers["location"] == "https://wip.local:8443/auth/logout"
    assert f"{app_origins.APP_COOKIE}=" in r.headers["set-cookie"]
    assert "max-age=0" in r.headers["set-cookie"].lower()


def test_userinfo_on_an_app_origin_reads_the_app_session(client):
    _, cookie = _handshake(client)
    r = _get(client, "/auth/userinfo", RC, {app_origins.APP_COOKIE: cookie})
    assert r.json()["email"] == "ann@wip.local"
    assert _get(client, "/auth/userinfo", RC, {"wip_session": _wip_session()}).status_code == 401


# ── apps at their origin's root (CASE-1103) ─────────────────────────────


def test_a_handoff_without_a_usable_return_path_lands_at_the_apps_root(client):
    code = app_origins.mint_code("rc", {"email": "u@x", "groups": [], "exp": time.time() + 3600})
    r = _get(client, f"/auth/handoff?code={code}&return_to=//evil.example/", RC)
    assert r.status_code == 302 and r.headers["location"] == "/"


def test_a_login_without_a_return_path_lands_on_wips_bare_host():
    assert settings.default_redirect == "/"


def test_the_wip_session_cookie_is_secure_on_an_https_install():
    """CASE-1105: browsers stored wip_session without Secure although WIP is
    served over https. The flag follows the public URL's scheme."""
    from starlette.middleware.sessions import SessionMiddleware

    session = next(m for m in app.user_middleware if m.cls is SessionMiddleware)
    assert session.kwargs["https_only"] is True
    assert session.kwargs["same_site"] == "lax"
