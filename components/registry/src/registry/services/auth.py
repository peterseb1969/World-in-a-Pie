"""Authentication for the Registry API.

This module provides authentication using the wip-auth shared library.
It re-exports the common auth functions for backward compatibility.
"""

from fastapi import Depends, HTTPException, Request

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


async def require_privileged_key(
    identity: UserIdentity = Depends(require_api_key),
) -> UserIdentity:
    """Gate for the identity-allocation protocol (register, provision,
    reserve, activate, entry update and delete).

    Those routes are the stores' internal contract: a store allocates an
    entity's identity here and commits the entity itself. A caller that
    allocated directly would create an identity with nothing behind it,
    could pre-claim a composite key that the store's later create then
    silently adopts, or could retire an entry the store still holds as
    active. So only the platform's privileged callers (wip-admins,
    wip-services) may use them; app keys create entities through the
    stores.
    """
    if not identity.has_any_group(["wip-admins", "wip-services"]):
        raise HTTPException(
            status_code=403,
            detail=(
                "Registry entry allocation is restricted to wip-admins / "
                "wip-services; create entities through their store instead"
            ),
        )
    return identity

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
    "require_privileged_key",
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
