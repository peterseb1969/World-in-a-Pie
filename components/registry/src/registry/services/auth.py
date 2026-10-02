"""Authentication for the Registry API.

This module provides authentication using the wip-auth shared library.
It re-exports the common auth functions for backward compatibility.
"""

from fastapi import Request

from wip_auth import (
    AuthConfig,
    UserIdentity,
    get_actor_info,
    get_auth_config,
    get_identity_owner,
    get_identity_string,
    optional_identity,
    require_admin,
    require_api_key,
    require_groups,
    require_identity,
    reset_auth_config,
    set_auth_config,
)


async def require_admin_key(request: Request) -> str:
    """Admin gate for the Registry's privileged endpoints.

    ``require_admin`` is a FACTORY: calling it returns the actual
    group-checking dependency. This used to be a bare alias of the
    uncalled factory — FastAPI then received the inner closure as the
    dependency's VALUE, the check never executed, and every
    authenticated caller passed every admin endpoint (including minting
    arbitrary-group API keys). This wrapper exists to make that shape
    impossible: the factory is invoked per request, so the check always
    runs and the admin-group set is read from the live auth config
    rather than frozen at import.

    Returns the caller's raw API key header — the namespace export and
    import endpoints forward it to the sibling stores as their outbound
    credential. An admin authenticated via OIDC JWT has no API key to
    forward; those two endpoints need an API-key caller, as they always
    did.
    """
    from wip_auth import require_admin_identity

    await require_admin_identity(request)
    return request.headers.get("X-API-Key", "")

# Re-export for backward compatibility
__all__ = [
    "AuthConfig",
    "UserIdentity",
    "get_actor_info",
    "get_auth_config",
    "get_identity_owner",
    "get_identity_string",
    "optional_identity",
    "require_admin",
    "require_admin_key",
    "require_api_key",
    "require_groups",
    "require_identity",
    "reset_auth_config",
    "set_auth_config",
]


# Legacy compatibility: AuthService class that was previously defined here
class AuthService:
    """Legacy compatibility wrapper.

    The new wip-auth library handles authentication via middleware.
    This class is kept for any code that still references it during
    initialization, but it's now a no-op.
    """

    @classmethod
    def initialize(cls, master_key: str | None = None) -> None:
        """Initialize auth service (no-op, handled by wip-auth middleware)."""
        pass

    @staticmethod
    def validate_api_key(api_key: str, require_admin: bool = False) -> bool:
        """Legacy validation method - always returns True.

        Authentication is now handled by wip-auth middleware.
        """
        return True
