"""WIP Auth Gateway — centralized OIDC gateway called by Caddy/nginx.

Called by Caddy's forward_auth (compose) or nginx-ingress's auth-url
(k8s) on every auth-protected request. The gateway checks the session
cookie and returns:

  - 200 with X-WIP-User / X-WIP-Groups / X-API-Key headers (authenticated)
  - 401 with X-Auth-Redirect header naming the login URL (unauthenticated)

The 401 response is the standard signal both Caddy and nginx know how
to turn into a browser redirect to /auth/login:
  - Caddy: `handle_response @401 { redir ... }` in the forward_auth block.
  - nginx: `auth-signin` annotation on the Ingress.

The OIDC flow (login → Dex → callback → session) is handled entirely by
this service. Apps never touch OIDC — they read identity from headers.
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.sessions import SessionMiddleware

from .config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("auth_gateway")

app = FastAPI(title="WIP Auth Gateway", version="0.1.0")

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="wip_session",
    max_age=settings.session_max_age,
    same_site="lax",
    https_only=False,  # Allow HTTP for internal forward_auth subrequests
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "auth-gateway"}


# ---------------------------------------------------------------------------
# /auth/verify — called by Caddy forward_auth / nginx auth-request
# ---------------------------------------------------------------------------

@app.get("/auth/verify")
async def verify(request: Request):
    """Check session cookie.

    Authenticated  → 200 with X-WIP-User / X-WIP-Groups / X-API-Key headers.
    Unauthenticated → 401 with X-Auth-Redirect header pointing at /auth/login.

    The 401 response is the standard signal both nginx's auth-request
    module (via the `auth-signin` annotation) and Caddy's `forward_auth`
    (via `handle_response @401 { redir ... }`) know how to turn into a
    browser redirect. Keeping the gateway target-agnostic — it never
    initiates a redirect itself — means renderers own the target-
    specific UX and the gateway code stays uniform.
    """
    session = request.session
    email = session.get("email")
    exp = session.get("exp", 0)

    if email and time.time() < exp:
        groups = ",".join(session.get("groups", []))
        return Response(
            status_code=200,
            headers={
                "X-WIP-User": email,
                "X-WIP-Groups": groups,
                "X-API-Key": settings.api_key,
            },
        )

    # Not authenticated. Build the return URL from forwarded headers for
    # the informational X-Auth-Redirect header — the renderer-side
    # redirect will append its own `return_to`.
    original_uri = request.headers.get("X-Forwarded-Uri", "/")
    original_host = request.headers.get("X-Forwarded-Host", settings.wip_hostname)
    original_proto = request.headers.get("X-Forwarded-Proto", "https")
    return_to = f"{original_proto}://{original_host}{original_uri}"

    from urllib.parse import quote
    login_url = f"/auth/login?return_to={quote(return_to)}"
    return Response(
        status_code=401,
        headers={"X-Auth-Redirect": login_url},
    )


# ---------------------------------------------------------------------------
# /auth/login — redirect to Dex
# ---------------------------------------------------------------------------

@app.get("/auth/login")
async def login(request: Request, return_to: str = ""):
    """Build OIDC authorization URL and redirect to Dex."""
    from .oidc import get_auth_url

    if not return_to:
        return_to = settings.default_redirect

    # Store return URL in session so callback can redirect back.
    request.session["return_to"] = return_to

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

    # Populate session.
    request.session["email"] = user_info["email"]
    request.session["groups"] = user_info.get("groups", [])
    request.session["name"] = user_info.get("name", "")
    request.session["user_id"] = user_info.get("sub", "")
    request.session["exp"] = int(time.time()) + settings.session_max_age
    if user_info.get("refresh_token"):
        request.session["refresh_token"] = user_info["refresh_token"]

    return_to = request.session.pop("return_to", settings.default_redirect)
    logger.info("Login successful: %s (groups: %s)", user_info["email"], user_info.get("groups"))
    return RedirectResponse(url=return_to, status_code=302)


# ---------------------------------------------------------------------------
# /auth/logout
# ---------------------------------------------------------------------------

@app.get("/auth/logout")
async def logout(request: Request):
    """Clear session and redirect to login page."""
    request.session.clear()
    return RedirectResponse(url=f"https://{settings.wip_hostname}:8443/", status_code=302)


# ---------------------------------------------------------------------------
# /auth/userinfo
# ---------------------------------------------------------------------------

@app.get("/auth/userinfo")
async def userinfo(request: Request):
    """Return current user info from session (for app UIs)."""
    session = request.session
    email = session.get("email")

    if not email or time.time() >= session.get("exp", 0):
        return JSONResponse(status_code=401, content={"authenticated": False})

    return {
        "authenticated": True,
        "email": email,
        "name": session.get("name", ""),
        "user_id": session.get("user_id", ""),
        "groups": session.get("groups", []),
    }
