"""Authentication middleware for FastAPI.

The middleware intercepts all requests and attempts authentication using
the configured providers. The authenticated identity is stored in a
context variable for access by route handlers.
"""

from collections.abc import Sequence

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from .identity import reset_current_identity, set_current_identity
from .models import UserIdentity
from .providers.base import AuthProvider

# Universal lifecycle endpoints — health probes, OpenAPI docs, the bare
# root. The middleware skips provider iteration for these so a wrong
# (but-present) credential header doesn't 401 a route that's meant to
# be reachable by external monitors and pre-configured clients with a
# stale key. Per CASE-60.
_DEFAULT_PUBLIC_PATHS = frozenset({
    "/", "/health", "/ready", "/docs", "/redoc", "/openapi.json",
})


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that authenticates requests using configured providers.

    The middleware tries each provider in order until one returns an identity.
    If no provider returns an identity and authentication is required, the
    request proceeds without an identity (route handlers use dependencies
    to enforce authentication).

    The middleware does NOT reject unauthenticated requests - that's the
    responsibility of route-level dependencies (require_identity, etc.).
    This allows mixing public and protected routes.

    For paths in `public_paths`, the middleware skips provider iteration
    entirely — useful for health probes that must be reachable even when
    a caller sends a stale or wrong API key (CASE-60). Universal endpoints
    (`/health`, `/ready`, `/docs`, ...) are always public; pass
    `public_paths` to add service-specific routes (e.g., the api-prefixed
    `/api/<svc>/health`).
    """

    def __init__(
        self,
        app,
        providers: Sequence[AuthProvider],
        public_paths: Sequence[str] | None = None,
    ):
        """Initialize the middleware.

        Args:
            app: The FastAPI application
            providers: List of auth providers to try in order
            public_paths: Additional paths (exact match) for which the
                middleware skips provider iteration. Universal endpoints
                are always added.
        """
        super().__init__(app)
        self.providers = list(providers)
        self.public_paths: frozenset[str] = _DEFAULT_PUBLIC_PATHS | frozenset(
            public_paths or []
        )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Process the request through auth providers.

        Args:
            request: The incoming request
            call_next: Function to call the next middleware/route

        Returns:
            The response from downstream handlers
        """
        # Public paths skip provider iteration. A wrong X-API-Key on
        # /health (or any other public route) should not 401 — those
        # routes are meant for external monitors and stale-key clients
        # (CASE-60).
        if request.url.path in self.public_paths:
            token = set_current_identity(None)
            try:
                return await call_next(request)
            finally:
                reset_current_identity(token)

        identity: UserIdentity | None = None

        # Try each provider until one authenticates
        for provider in self.providers:
            try:
                identity = await provider.authenticate(request)
                if identity is not None:
                    break
            except HTTPException as exc:
                # Provider raised an HTTPException (e.g., invalid credentials)
                # Convert to a proper JSON response
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                    headers=exc.headers,
                )
            except Exception:
                # Unexpected error - re-raise
                raise

        # Store identity in context for route handlers.
        # Use token-based save/restore so nested in-process calls
        # (e.g., Registry via ASGITransport) don't wipe the outer identity.
        token = set_current_identity(identity)

        try:
            response = await call_next(request)
            return response
        finally:
            reset_current_identity(token)


def create_auth_middleware(
    providers: Sequence[AuthProvider],
    public_paths: Sequence[str] | None = None,
) -> type[AuthMiddleware]:
    """Create a configured auth middleware class.

    This factory function creates a middleware class with the providers
    pre-configured. Use this with FastAPI's add_middleware.

    Args:
        providers: List of auth providers to use
        public_paths: Additional paths (beyond the universal lifecycle
            endpoints) for which the middleware skips provider iteration.
            Pass the api-prefixed health route here, e.g.,
            `["/api/registry/health"]`. See CASE-60.

    Returns:
        Configured middleware class

    Example:
        providers = [APIKeyProvider(keys), OIDCProvider(issuer_url="...")]
        app.add_middleware(create_auth_middleware(
            providers,
            public_paths=["/api/registry/health"],
        ))
    """
    class ConfiguredAuthMiddleware(AuthMiddleware):
        def __init__(self, app):
            super().__init__(app, providers, public_paths)

    return ConfiguredAuthMiddleware
