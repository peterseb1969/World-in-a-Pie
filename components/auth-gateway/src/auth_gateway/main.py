"""WIP Auth Gateway — centralized OIDC gateway behind wip-caddy's forward_auth.

Called by wip-caddy's forward_auth on every auth-protected request, on every
target (on k8s wip-caddy sits behind one catch-all Ingress; the app edge
calls it the same way for attached apps). The gateway checks the session
cookie and returns:

  - 200 with X-WIP-User / X-WIP-Groups headers (authenticated) — the
    user's identity only; the gateway holds no API key and hands none on
  - 401 with X-Auth-Redirect header naming the login URL (unauthenticated)

The 401 response is what wip-caddy turns into the browser redirect to
/auth/login (`handle_response @401 { redir ... }` in the forward_auth
block). A hand-built nginx `auth_request` setup, which the deployer does not
render, would use the `auth-signin` annotation the same way.

Proxies that cannot own that redirect (Traefik's forwardAuth passes
non-2xx auth responses to the client verbatim) set
AUTH_REDIRECT_MODE=redirect instead, and the gateway 302s browser
requests to the absolute public login URL itself — see
Settings.auth_redirect_mode in config.py for the full contract.

The OIDC flow (login → Dex → callback → session) is handled entirely by
this service. Apps never touch OIDC — they read identity from headers.
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware

from wip_auth.ratelimit import setup_rate_limiting

from . import app_origins
from .config import (
    check_production_security,
    check_redirect_mode,
    settings,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("auth_gateway")

# Refuse to start in prod with the forgeable default session secret —
# before the SessionMiddleware below is ever constructed with it.
check_production_security()

# Refuse to start on a mistyped AUTH_REDIRECT_MODE — a typo silently
# falling back to 401 behaviour would surface far from its cause.
check_redirect_mode()

app = FastAPI(title="WIP Auth Gateway", version="0.1.0")

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="wip_session",
    max_age=settings.session_max_age,
    same_site="lax",
    # Secure whenever browsers reach WIP over https (every install the
    # deployer renders). The attribute only governs what the browser sends
    # back; the gateway reads the cookie from the forward_auth subrequest's
    # header either way, so the internal plain-HTTP hop is unaffected.
    https_only=app_origins.cookie_secure(),
)

# Per-client rate limit, the same rule and the same WIP_RATE_LIMIT as every
# WIP service: a request that came through Caddy (it carries
# X-Forwarded-For) is limited per client address; one without it, and the
# health probe, is not. Added after the session middleware so it runs first
# and a flood costs no session parsing.
#
# /auth/verify answers once per protected request, for every service and
# every app, so this one budget is a client's platform-wide cap on protected
# routes: a client spreading requests over several services reaches it
# before any single service's. Behind Caddy a refused verify reaches the
# client as the 429 with its Retry-After; behind a hand-built nginx
# auth_request (not rendered by the deployer) it becomes a 500, as any
# non-401/403 answer does there.
setup_rate_limiting(app)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "auth-gateway"}


# ---------------------------------------------------------------------------
# /auth/verify — called by wip-caddy's forward_auth (and the app edge's)
# ---------------------------------------------------------------------------

@app.get("/auth/verify")
async def verify(request: Request):
    """Check session cookie.

    Authenticated  → 200 with X-WIP-User / X-WIP-Groups headers.
    Unauthenticated → 401 with X-Auth-Redirect header pointing at
    /auth/login — or, in AUTH_REDIRECT_MODE=redirect, a 302 to the
    absolute public login URL for browser requests.

    The 401 response is the signal wip-caddy's `forward_auth` (via
    `handle_response @401 { redir ... }`) turns into a browser redirect —
    the proxy owns the UX. Behind a hand-built nginx auth_request setup,
    which the deployer does not render, the 401 is the ONLY safe answer (auth_request treats any other
    non-2xx status, a 302 included, as an internal error → 500). The
    "redirect" mode exists solely for proxy families that pass the auth
    response to the client verbatim and therefore cannot own the
    redirect themselves (Traefik forwardAuth). Which mode applies is
    deployment configuration, never request sniffing: every proxy
    forwards the browser's Accept header to the auth subrequest, so a
    header heuristic cannot tell the constellations apart.
    """
    label = app_origins.request_label(request.headers)
    if label is not None:
        return _verify_app_origin(request, label)

    session = request.session
    email = session.get("email")
    exp = session.get("exp", 0)

    if email and time.time() < exp:
        # The route's allowed groups arrive in the verify URL the deployer
        # renders (wip-caddy's forward_auth `uri`); the client
        # cannot set them. A logged-in user outside them is refused with
        # 403 — never 401, which would send them back through a login they
        # already completed.
        return _allowed_or_forbidden(request, email, session.get("groups", []))

    # Not authenticated. Build the return URL from forwarded headers for
    # the informational X-Auth-Redirect header — the renderer-side
    # redirect will append its own `return_to`.
    original_uri = request.headers.get("X-Forwarded-Uri", "/")
    original_host = request.headers.get("X-Forwarded-Host", settings.wip_hostname)
    original_proto = request.headers.get("X-Forwarded-Proto", "https")
    return_to = f"{original_proto}://{original_host}{original_uri}"

    from urllib.parse import quote
    login_path = f"/auth/login?return_to={quote(return_to)}"

    # "redirect" mode only: the proxy passes this response to the
    # browser verbatim, so the gateway must issue the login redirect
    # itself — absolute, because the browser resolves it, not the
    # proxy. API callers (no text/html Accept) keep the 401 contract.
    if (
        settings.auth_redirect_mode == "redirect"
        and "text/html" in request.headers.get("Accept", "")
    ):
        return RedirectResponse(
            url=f"{original_proto}://{original_host}{login_path}",
            status_code=302,
        )

    return Response(
        status_code=401,
        headers={"X-Auth-Redirect": login_path},
    )


def _allowed_or_forbidden(request: Request, email: str, user_groups: list[str]) -> Response:
    """200 with the identity, or 403 when the route's allowed groups (in the
    verify URL the deployer renders) exclude the user."""
    allowed = [g for g in request.query_params.get("allowed_groups", "").split(",") if g]
    if allowed and not set(allowed).intersection(user_groups):
        logger.info("Access refused: %s (groups %s) not in %s", email, user_groups, allowed)
        return _forbidden(request)
    return Response(
        status_code=200,
        headers={"X-WIP-User": email, "X-WIP-Groups": ",".join(user_groups)},
    )


def _verify_app_origin(request: Request, label: str) -> Response:
    """verify on an app origin: only an app session naming this app counts.
    Without one, the browser is sent through the handshake on WIP's origin
    (absolute: it is another origin)."""
    data = app_origins.read_session(request.cookies, label)
    if data is not None:
        return _allowed_or_forbidden(request, data["email"], data.get("groups", []))
    login_url = app_origins.app_login_url(label, request.headers.get("X-Forwarded-Uri", "/"))
    if (
        settings.auth_redirect_mode == "redirect"
        and "text/html" in request.headers.get("Accept", "")
    ):
        return RedirectResponse(url=login_url, status_code=302)
    return Response(status_code=401, headers={"X-Auth-Redirect": login_url})


_FORBIDDEN_PAGE ="""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>No access</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: system-ui, sans-serif; max-width: 32rem; margin: 4rem auto; padding: 0 1rem">
<h1>You don't have access to this app</h1>
<p>You are signed in, but your account is not in a group this app allows.
Ask an administrator to add you, or <a href="/auth/logout">sign in as someone else</a>.</p>
</body></html>
"""


def _forbidden(request: Request) -> Response:
    """403 for a logged-in user outside a route's allowed groups.

    A readable page for browsers, JSON for API callers. wip-caddy passes
    the body to the client; a hand-built nginx auth_request setup would
    pass only the status (nginx serves its own 403 page).
    """
    if "text/html" in request.headers.get("Accept", ""):
        return HTMLResponse(_FORBIDDEN_PAGE, status_code=403)
    return JSONResponse(
        status_code=403,
        content={"error": "forbidden", "detail": "You don't have access to this app"},
    )


# ---------------------------------------------------------------------------
# /auth/login — redirect to Dex
# ---------------------------------------------------------------------------

@app.get("/auth/login")
async def login(request: Request, return_to: str = ""):
    """Build OIDC authorization URL and redirect to Dex."""
    from .oidc import get_auth_url

    # Store return URL in session so callback can redirect back. Only a
    # place on this install: a login link must never end on another site.
    request.session["return_to"] = _safe_return_to(return_to)

    auth_url = await get_auth_url(request)
    return RedirectResponse(url=auth_url)


# ---------------------------------------------------------------------------
# /auth/callback — Dex redirects here after login
# ---------------------------------------------------------------------------

@app.get("/auth/callback")
async def callback(request: Request):
    """Exchange authorization code for tokens, create session, redirect."""
    from .oidc import exchange_code

    try:
        user_info = await exchange_code(request)
    except ValueError as exc:
        # State mismatch is usually a multi-tab race: another tab completed
        # login and overwrote this tab's oauth_state in the shared session.
        # If the user is already authenticated (from the other tab), just
        # redirect to the app instead of showing an error.
        if "state" in str(exc).lower():
            email = request.session.get("email")
            if email and time.time() < request.session.get("exp", 0):
                logger.info("Stale OIDC state but already authenticated as %s — redirecting", email)
                return RedirectResponse(url=settings.default_redirect, status_code=302)
            # Not authenticated — start fresh login
            logger.info("Stale OIDC state, not authenticated — restarting login")
            return RedirectResponse(url="/auth/login", status_code=302)
        logger.error("OIDC callback failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": "Authentication failed", "detail": str(exc)},
        )
    except Exception as exc:
        logger.error("OIDC callback failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"error": "Authentication failed", "detail": str(exc)},
        )

    # The email travels as X-WIP-User on every request. One the header
    # cannot carry would turn each of them into a 500, so refuse it here,
    # once, with a reason.
    from .oidc import header_safe

    if not header_safe(user_info["email"]):
        logger.warning("Login refused: email %r cannot be carried in X-WIP-User", user_info["email"])
        return JSONResponse(
            status_code=403,
            content={
                "error": "forbidden",
                "detail": "This account's email cannot be carried in WIP's identity header "
                "(it holds characters outside latin-1 or control characters).",
            },
        )

    # Populate session.
    request.session["email"] = user_info["email"]
    request.session["groups"] = user_info.get("groups", [])
    request.session["name"] = user_info.get("name", "")
    request.session["user_id"] = user_info.get("sub", "")
    request.session["exp"] = int(time.time()) + settings.session_max_age
    if user_info.get("refresh_token"):
        request.session["refresh_token"] = user_info["refresh_token"]

    return_to = _safe_return_to(request.session.pop("return_to", ""))
    logger.info("Login successful: %s (groups: %s)", user_info["email"], user_info.get("groups"))
    return RedirectResponse(url=return_to, status_code=302)


# ---------------------------------------------------------------------------
# /auth/logout
# ---------------------------------------------------------------------------

def _same_origin_path(return_to: str) -> bool:
    """True only for a same-origin PATH a logout may redirect back to.

    A protocol-relative "//host/..." (or its backslash cousin, which browsers
    normalize to the same thing) also starts with "/" but sends the browser
    off-site — an open redirect on the logout door. Absolute URLs and empty
    values are rejected too; the caller falls back to the site root.

    Control characters are refused as well: browsers delete tabs and
    newlines from a URL, so "/\\t/evil.host" would arrive as "//evil.host".
    """
    return bool(
        return_to
        and return_to.startswith("/")
        and not return_to.startswith(("//", "/\\"))
        and not _has_control_characters(return_to)
    )


def _has_control_characters(value: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)


def _safe_return_to(return_to: str) -> str:
    """Where a login may send the browser once it completes.

    A same-origin path (logout's rule), or an absolute http(s) URL on this
    install's own host (WIP_HOSTNAME, any port): the gateway's redirect mode
    builds that form itself from the forwarded headers. Anything else, empty
    included, becomes the default redirect, so a crafted login link cannot
    end on another site after a genuine login.
    """
    if _same_origin_path(return_to):
        return return_to
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(return_to)
        hostname = parts.hostname
    except ValueError:
        return settings.default_redirect
    if (
        parts.scheme in ("http", "https")
        and hostname is not None
        and hostname == settings.wip_hostname.lower()
        and parts.username is None
        and parts.password is None
        and not _has_control_characters(return_to)
    ):
        return return_to
    return settings.default_redirect


@app.get("/auth/logout")
async def logout(request: Request, return_to: str = ""):
    """Clear session and redirect back to the app (or site root).

    On an app origin: drop that origin's app session and continue to the
    logout on WIP's origin, which ends the login itself. Other apps' sessions
    end when they expire (APP_SESSION_MAX_AGE)."""
    if app_origins.request_label(request.headers) is not None:
        response = RedirectResponse(url=f"{app_origins.wip_origin()}/auth/logout", status_code=302)
        response.delete_cookie(app_origins.APP_COOKIE, path="/")
        return response
    request.session.clear()
    if _same_origin_path(return_to):
        return Response(status_code=302, headers={"Location": return_to})
    proto = request.headers.get("X-Forwarded-Proto", "https")
    host = request.headers.get("X-Forwarded-Host", settings.wip_hostname)
    return RedirectResponse(url=f"{proto}://{host}/", status_code=302)


# ---------------------------------------------------------------------------
# /auth/userinfo
# ---------------------------------------------------------------------------

@app.get("/auth/userinfo")
async def userinfo(request: Request):
    """Return current user info from session (for app UIs)."""
    label = app_origins.request_label(request.headers)
    session = app_origins.read_session(request.cookies, label) if label else request.session
    email = (session or {}).get("email")

    if not session or not email or time.time() >= session.get("exp", 0):
        return JSONResponse(status_code=401, content={"authenticated": False})

    return {
        "authenticated": True,
        "email": email,
        "name": session.get("name", ""),
        "user_id": session.get("user_id", ""),
        "groups": session.get("groups", []),
    }


# ---------------------------------------------------------------------------
# App origins: the handshake, see app_origins.py
# ---------------------------------------------------------------------------

@app.get("/auth/app-login")
async def app_login(request: Request, app: str = "", return_to: str = "/", silent: str = ""):
    """On WIP's origin: hand the logged-in user to an app origin with a
    one-time code. Not logged in: a normal login first, then back here; or,
    for @wip/react's silent renew, straight back with login_required."""
    if not app_origins.LABEL_RE.fullmatch(app):
        return JSONResponse(status_code=400, content={"error": "invalid_app"})
    if not app_origins.same_origin_path(return_to):
        return_to = "/"
    session = request.session
    if not session.get("email") or time.time() >= session.get("exp", 0):
        if silent:
            return RedirectResponse(
                url=f"{app_origins.app_origin(app)}{app_origins.RENEWED_PATH}?error=login_required",
                status_code=302,
            )
        from urllib.parse import quote

        here = f"/auth/app-login?app={app}&return_to={quote(return_to)}"
        return RedirectResponse(url=f"/auth/login?return_to={quote(here)}", status_code=302)
    from urllib.parse import quote

    code = app_origins.mint_code(app, session)
    return RedirectResponse(
        url=f"{app_origins.app_origin(app)}/auth/handoff?code={quote(code)}"
        f"&return_to={quote(return_to)}",
        status_code=302,
    )


@app.get("/auth/handoff")
async def handoff(request: Request, code: str = "", return_to: str = "/"):
    """On an app origin: redeem the one-time code and set that origin's app
    session, then continue to `return_to` (a path on this origin)."""
    label = app_origins.request_label(request.headers)
    if label is None:
        return JSONResponse(status_code=400, content={
            "error": "not_an_app_origin", "detail": "a handoff completes only on an app origin",
        })
    identity = app_origins.redeem_code(code, label)
    if identity is None:
        logger.info("App handoff refused for %s: the code is invalid, expired, used or not for it", label)
        if return_to == app_origins.RENEWED_PATH:
            return RedirectResponse(url=f"{return_to}?error=handoff_failed", status_code=302)
        return HTMLResponse(
            "<!doctype html><p>This sign-in link has expired. "
            f'<a href="{app_origins.app_login_url(label, "/")}">Sign in again</a>.</p>',
            status_code=400,
        )
    value, max_age = app_origins.session_cookie(identity, label)
    target = return_to if app_origins.same_origin_path(return_to) else "/"
    response = RedirectResponse(url=target, status_code=302)
    response.set_cookie(
        app_origins.APP_COOKIE, value, max_age=max_age, path="/", httponly=True,
        secure=app_origins.cookie_secure(), samesite="lax",
    )
    return response


@app.get("/auth/renew")
async def renew(request: Request):
    """Loaded by @wip/react in a hidden iframe on an app origin: run the
    handshake again without showing anything, ending at /auth/renewed. On
    WIP's own origin there is no app session to renew."""
    label = app_origins.request_label(request.headers)
    if label is None:
        return HTMLResponse(app_origins.renewed_page(True, 0, "not_an_app_origin"))
    return RedirectResponse(
        url=app_origins.app_login_url(label, app_origins.RENEWED_PATH, silent=True),
        status_code=302,
    )


@app.get("/auth/renewed")
async def renewed(request: Request, error: str = ""):
    """The end of a silent renew: tell the page whether the app session is
    fresh, and for how long."""
    label = app_origins.request_label(request.headers)
    data = app_origins.read_session(request.cookies, label) if label else None
    if error or data is None:
        return HTMLResponse(app_origins.renewed_page(False, 0, error or "no_session"))
    return HTMLResponse(app_origins.renewed_page(True, max(int(data["exp"] - time.time()), 0)))
