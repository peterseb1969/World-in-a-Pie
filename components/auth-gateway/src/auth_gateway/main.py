"""WIP Auth Gateway — Caddy forward_auth service for centralized OIDC.

This service sits between Caddy and WIP apps. Caddy calls /auth/verify on
every request to /apps/*. The gateway checks the session cookie and returns:
  - 200 with X-WIP-User, X-WIP-Groups, X-API-Key headers (authenticated)
  - 302 redirect to /auth/login (unauthenticated)

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
# /auth/verify — called by Caddy forward_auth
# ---------------------------------------------------------------------------

@app.get("/auth/verify")
async def verify(request: Request):
    """Check session cookie. Return 200 + identity headers or 302 to login."""
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

    # Not authenticated — build return URL from Caddy's forwarded headers.
    original_uri = request.headers.get("X-Forwarded-Uri", "/")
    original_host = request.headers.get("X-Forwarded-Host", settings.wip_hostname)
    original_proto = request.headers.get("X-Forwarded-Proto", "https")
    return_to = f"{original_proto}://{original_host}{original_uri}"

    from urllib.parse import quote
    login_url = f"/auth/login?return_to={quote(return_to)}"
    return RedirectResponse(url=login_url, status_code=302)


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
