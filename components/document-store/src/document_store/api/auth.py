"""Authentication for the Document Store API.

This module provides authentication using the wip-auth shared library.
It re-exports the common auth functions for backward compatibility.
"""

from wip_auth import (
    AuthConfig,
    UserIdentity,
    get_auth_config,
    get_identity_string,
    optional_identity,
    require_admin,
    require_api_key,
    require_groups,
    require_identity,
    reset_auth_config,
    set_auth_config,
)

# Re-export for backward compatibility
__all__ = [
    "AuthConfig",
    "UserIdentity",
    "get_auth_config",
    "get_identity_string",
    "optional_identity",
    "require_admin",
    "require_api_key",
    "require_groups",
    "require_identity",
    "reset_auth_config",
    "set_auth_config",
    "set_api_key",
]


def set_api_key(key: str) -> None:
    """Set the API key (for testing).

    This is a backward compatibility function. It configures
    the auth system to use a specific API key.
    """
    config = AuthConfig(
        mode="api_key_only",
        legacy_api_key=key,
    )
    set_auth_config(config)
