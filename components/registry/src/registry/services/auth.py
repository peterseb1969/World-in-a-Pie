"""Authentication for the Registry API.

This module provides authentication using the wip-auth shared library.
It re-exports the common auth functions for backward compatibility.
"""

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

# Alias for backward compatibility - require_admin replaces require_admin_key
require_admin_key = require_admin

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
