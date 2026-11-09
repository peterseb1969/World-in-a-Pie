"""Rate limiting for WIP services.

setup_rate_limiting installs a per-client limit (slowapi, built on the
limits library) on every request that reached the service through the
platform's reverse proxy:

- **Who is limited.** A request that came through Caddy carries the
  ``X-Forwarded-For`` header Caddy writes itself (a client's own copy is
  replaced, except from the trusted k8s ingress), and is limited per client
  address, its first entry.
- **Who is not.** A request without the header did not come through Caddy:
  it is a call from inside the platform (another service, a health probe,
  ingest's deliveries, a restore), and is not limited, so the platform never
  throttles its own bulk work. Only Caddy's port is published on compose and
  dev; on k8s, anything inside the cluster that calls a service directly is
  inside this exemption.
- **Probes.** A path ending in health, ready or livez is never limited.

The limit runs before authentication, so it costs no key check and also
sheds a flood of unknown keys. Buckets are in memory, per process: N
replicas allow N times the limit, and a restart resets them.

Configuration via environment:
    WIP_RATE_LIMIT=40000/minute   # Default: 40K req/min per client address
    WIP_RATE_LIMIT=                # Empty string disables rate limiting

Usage in a service's main.py, after setup_auth and before CORS (so the
limit runs before authentication, and preflights are answered by CORS
without spending budget):
    from wip_auth.ratelimit import setup_rate_limiting
    app = FastAPI(...)
    setup_auth(app, ...)
    setup_rate_limiting(app)
"""

import os
from typing import Any

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware, sync_check_limits
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Lifecycle probes, matched on the last path segment so a service's own
# prefixed probe (/api/<svc>/health) counts too.
_PROBE_SEGMENTS = frozenset({"health", "ready", "livez"})


def _get_client_ip(request: Request) -> str:
    """Get client IP, respecting X-Forwarded-For from reverse proxy."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


def create_limiter() -> Limiter:
    """Create a rate limiter with WIP default configuration.

    Uses WIP_RATE_LIMIT env var (default: "40000/minute").
    Set to empty string to disable rate limiting.
    """
    default_limit = os.getenv("WIP_RATE_LIMIT", "40000/minute")
    default_limits: Any = [default_limit] if default_limit else []

    return Limiter(
        key_func=_get_client_ip,
        default_limits=default_limits,
        enabled=bool(default_limit),
        headers_enabled=True,
        # Scope a budget by the limit target (_service), not by the request
        # path. slowapi's default ("url") gives every distinct path its own
        # budget, so a client walking document ids would never be limited.
        key_style="endpoint",
    )


def is_rate_limited(request: Request) -> bool:
    """Whether the limit applies: the request came through the reverse proxy
    (it carries X-Forwarded-For) and is not a lifecycle probe."""
    if request.url.path.rstrip("/").rsplit("/", 1)[-1] in _PROBE_SEGMENTS:
        return False
    return "x-forwarded-for" in request.headers


def _service() -> None:
    """The one limit target: the default limit counts a client's requests to
    the whole service, whichever route they reach."""


class WipRateLimitMiddleware(SlowAPIMiddleware):
    """slowapi's limit check, applied only where is_rate_limited says so.

    It does not use SlowAPIMiddleware.dispatch: that looks the route up among
    the app's routes, cannot see into a router added with include_router
    (FastAPI keeps those behind one _IncludedRouter entry), and exempts a
    request whose route it cannot find, which is every API route of every
    service. The check runs against _service instead, so each client has one
    budget per service.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        limiter = request.app.state.limiter
        if not limiter.enabled or not is_rate_limited(request):
            response: Response = await call_next(request)
            return response
        refused, inject = sync_check_limits(limiter, request, _service, request.app)
        if refused is not None:
            refusal: Response = refused
            return refusal
        response = await call_next(request)
        if inject:
            response = limiter._inject_headers(response, request.state.view_rate_limit)
        return response


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> Response:
    """429 with the retry window: Retry-After and the X-RateLimit-* headers.

    Synchronous on purpose: slowapi's middleware calls a synchronous handler
    and silently replaces an async one with its own.
    """
    response = JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded ({exc.detail}). Try again later."},
    )
    limited: Response = request.app.state.limiter._inject_headers(
        response, request.state.view_rate_limit
    )
    return limited


def setup_rate_limiting(app) -> Limiter | None:
    """Setup rate limiting for a FastAPI application.

    Reads WIP_RATE_LIMIT from environment (default: "40000/minute").
    Set to empty string to disable.

    Returns the limiter instance, or None if disabled.
    """
    rate_limit = os.getenv("WIP_RATE_LIMIT", "40000/minute")
    if not rate_limit:
        return None

    limiter = create_limiter()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_middleware(WipRateLimitMiddleware)
    return limiter
