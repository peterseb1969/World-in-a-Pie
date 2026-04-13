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


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware that authenticates requests using configured providers.

    The middleware tries each provider in order until one returns an identity.
    If no provider returns an identity and authentication is required, the
    request proceeds without an identity (route handlers use dependencies
    to enforce authentication).

    The middleware does NOT reject unauthenticated requests - that's the
    responsibility of route-level dependencies (require_identity, etc.).
    This allows mixing public and protected routes.
    """

    def __init__(self, app, providers: Sequence[AuthProvider]):
        """Initialize the middleware.

        Args:
            app: The FastAPI application
            providers: List of auth providers to try in order
        """
        super().__init__(app)
        self.providers = list(providers)

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
) -> type[AuthMiddleware]:
    """Create a configured auth middleware class.

    This factory function creates a middleware class with the providers
    pre-configured. Use this with FastAPI's add_middleware.

    Args:
        providers: List of auth providers to use

    Returns:
        Configured middleware class

    Example:
        providers = [APIKeyProvider(keys), OIDCProvider(issuer_url="...")]
        app.add_middleware(create_auth_middleware(providers))
    """
    class ConfiguredAuthMiddleware(AuthMiddleware):
        def __init__(self, app):
            super().__init__(app, providers)

    return ConfiguredAuthMiddleware
