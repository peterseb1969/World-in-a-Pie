"""Apps on origins of their own (WIP_APP_ORIGINS=subdomain).

Served under /apps/<label> on WIP's own origin, every app shares the
visitor's wip_session cookie: an app's server receives it, and an app's
JavaScript can call any other app as the visitor. With app origins each app
lives at https://<label>.<hostname>[:port] and gets a session of its own:

1. A request on an app origin without a valid app session is sent to
   /auth/app-login on WIP's origin (the proxy turns verify's 401 into that
   redirect, or the gateway does in redirect mode).
2. With a valid wip_session (after a normal login if needed), the gateway
   mints a one-time code: signed, valid for CODE_MAX_AGE seconds, usable
   once, bound to the app. It redirects to /auth/handoff on the app origin.
3. /auth/handoff, routed to the gateway on the app origin, redeems the code
   and sets wip_app_session there: host-only, HttpOnly, naming the app,
   expiring after APP_SESSION_MAX_AGE (never after the WIP session).
4. /auth/verify on an app origin accepts only an app session naming that
   origin's app, so an app's server cannot replay its visitors' sessions at
   another app.

Logout on WIP's origin reaches an app when its session expires. @wip/react
keeps an open app's session alive by loading /auth/renew in a hidden iframe:
it re-runs the handshake silently and /auth/renewed reports the result to
the page with postMessage.

Only WIP's origin talks to Dex, so Dex needs no client or redirect URI per
app. The code store is in memory: the gateway runs as one replica.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import quote, urlsplit

from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import settings

APP_COOKIE = "wip_app_session"
CODE_MAX_AGE = 30
# One DNS label: what /apps/<label> names, and what <label>.<hostname> carries.
LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
RENEWED_PATH = "/auth/renewed"

_used_codes: dict[str, float] = {}
_used_lock = threading.Lock()


def enabled() -> bool:
    return settings.app_origins == "subdomain"


def _public() -> tuple[str, str, int | None]:
    """(scheme, hostname, port or None) of WIP's own public URL."""
    url = settings.public_url or settings.callback_url
    parts = urlsplit(url)
    return parts.scheme or "https", (parts.hostname or settings.wip_hostname).lower(), parts.port


def _origin(host: str) -> str:
    scheme, _, port = _public()
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{host}" + ("" if port in (None, default) else f":{port}")


def wip_origin() -> str:
    return _origin(_public()[1])


def app_origin(label: str) -> str:
    return _origin(f"{label}.{_public()[1]}")


def label_of(host: str | None) -> str | None:
    """The app label of an app origin's Host, or None for any other host."""
    if not host:
        return None
    name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    name = name.lower().rstrip(".")
    suffix = "." + _public()[1]
    if not name.endswith(suffix):
        return None
    label = name[: -len(suffix)]
    return label if LABEL_RE.fullmatch(label) else None


def request_label(headers: Any) -> str | None:
    """The app label of the origin a request was made to. The proxy sets
    X-Forwarded-Host from the request's own Host (it never forwards a
    client's), so it names the origin the browser addressed."""
    if not enabled():
        return None
    return label_of(headers.get("X-Forwarded-Host") or headers.get("Host"))


def same_origin_path(value: str) -> bool:
    return bool(
        value
        and value.startswith("/")
        and not value.startswith(("//", "/\\"))
        and not any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)
    )


def app_login_url(label: str, return_to: str, *, silent: bool = False) -> str:
    query = f"app={label}&return_to={quote(return_to if same_origin_path(return_to) else '/')}"
    return f"{wip_origin()}/auth/app-login?{query}" + ("&silent=1" if silent else "")


# ── the one-time code and the app session ─────────────────────────────────


def _codes() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="wip-app-handoff")


def _sessions() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="wip-app-session")


def mint_code(label: str, session: dict[str, Any]) -> str:
    return _codes().dumps({
        "app": label,
        "nonce": secrets.token_urlsafe(16),
        "email": session.get("email"),
        "groups": session.get("groups", []),
        "name": session.get("name", ""),
        "user_id": session.get("user_id", ""),
        "sess_exp": int(session.get("exp", 0)),
    })


def redeem_code(code: str, label: str) -> dict[str, Any] | None:
    """The identity a code carries, if it is genuine, fresh, unused and made
    for `label`. A redeemed code can never be redeemed again."""
    try:
        data = _codes().loads(code, max_age=CODE_MAX_AGE)
    except BadSignature:
        return None
    if not isinstance(data, dict) or data.get("app") != label or not data.get("email"):
        return None
    now = time.time()
    with _used_lock:
        for nonce in [n for n, exp in _used_codes.items() if exp < now]:
            del _used_codes[nonce]
        if data["nonce"] in _used_codes:
            return None
        _used_codes[data["nonce"]] = now + CODE_MAX_AGE + 5
    return data


def session_cookie(identity: dict[str, Any], label: str) -> tuple[str, int]:
    """(cookie value, max-age) of an app session for `label`."""
    now = int(time.time())
    exp = min(now + settings.app_session_max_age, int(identity.get("sess_exp", 0)))
    value = _sessions().dumps({
        "app": label,
        "email": identity["email"],
        "groups": identity.get("groups", []),
        "name": identity.get("name", ""),
        "user_id": identity.get("user_id", ""),
        "exp": exp,
    })
    return value, max(exp - now, 0)


def read_session(cookies: dict[str, str], label: str) -> dict[str, Any] | None:
    """The app session the request carries for `label`, or None when it is
    missing, forged, expired, or made for another app."""
    raw = cookies.get(APP_COOKIE)
    if not raw:
        return None
    try:
        data = _sessions().loads(raw)
    except BadSignature:
        return None
    if not isinstance(data, dict) or data.get("app") != label:
        return None
    if time.time() >= data.get("exp", 0) or not data.get("email"):
        return None
    return data


def cookie_secure() -> bool:
    return _public()[0] == "https"


def renewed_page(ok: bool, expires_in: int, reason: str = "") -> str:
    """What /auth/renewed shows in @wip/react's hidden iframe: one message to
    the page that loaded it (same origin), nothing else."""
    message = json.dumps({"type": "wip-app-session", "ok": ok,
                          "expires_in": expires_in, "reason": reason})
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"></head><body><script>"
        f"window.parent.postMessage({message}, window.location.origin);"
        "</script></body></html>"
    )
