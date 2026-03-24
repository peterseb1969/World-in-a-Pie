"""Rate limiting configuration for WIP services.

Provides a shared rate limiter factory so all services use consistent
configuration. Uses slowapi (built on the limits library).

Configuration via environment:
    WIP_RATE_LIMIT=40000/minute   # Default: 40K req/min per IP
    WIP_RATE_LIMIT=                # Empty string disables rate limiting

Usage in a service's main.py:
    from wip_auth.ratelimit import setup_rate_limiting
    app = FastAPI(...)
    setup_rate_limiting(app)
"""

import os
from typing import Any

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse


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
    )


def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Custom 429 handler with retry information."""
    return JSONResponse(
        status_code=429,
        content={
            "detail": "Rate limit exceeded. Try again later.",
        },
    )


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
    return limiter
