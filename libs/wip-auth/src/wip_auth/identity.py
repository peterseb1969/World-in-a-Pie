"""Identity context management.

Provides a request-scoped context for storing the current authenticated identity.
This allows middleware to set the identity and dependencies to access it.
"""

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import UserIdentity

# Context variable for the current request's identity
_current_identity: ContextVar["UserIdentity | None"] = ContextVar(
    "current_identity", default=None
)


def get_current_identity() -> "UserIdentity | None":
    """Get the authenticated identity for the current request.

    Returns:
        The UserIdentity if authenticated, None otherwise
    """
    return _current_identity.get()


def set_current_identity(identity: "UserIdentity | None") -> "Token[UserIdentity | None]":
    """Set the authenticated identity for the current request.

    This should only be called by the auth middleware.
    Returns a token that can be passed to reset_current_identity()
    to restore the previous value (important for in-process transport
    injection where nested middleware calls share the same ContextVar).

    Args:
        identity: The authenticated identity or None

    Returns:
        A ContextVar token for restoring the previous value
    """
    return _current_identity.set(identity)


def reset_current_identity(token: "Token[UserIdentity | None]") -> None:
    """Reset identity to its previous value using a saved token.

    This restores the ContextVar to whatever it was before the
    corresponding set_current_identity() call. This is essential
    for in-process transport injection where Registry and service
    apps share the same ContextVar — without it, Registry's middleware
    clears the service's identity.
    """
    _current_identity.reset(token)


def clear_current_identity() -> None:
    """Clear the current identity.

    Called after request processing to clean up.
    """
    _current_identity.set(None)


def get_identity_string() -> str:
    """Get the identity string for created_by/updated_by fields.

    Returns:
        Identity string (e.g., "user:123", "apikey:service", or "anonymous")
    """
    identity = get_current_identity()
    if identity:
        return identity.identity_string
    return "anonymous"


def get_identity_owner() -> str | None:
    """Get the owner of the current identity.

    For API keys, this is the configured owner (e.g., "admin@wip.local").
    For JWT users, this is the email or user_id.
    For anonymous, returns None.

    Returns:
        Owner string or None
    """
    identity = get_current_identity()
    if identity is None:
        return None

    # For API keys, owner is in raw_claims
    if identity.auth_method == "api_key":
        raw_claims = identity.raw_claims or {}
        return raw_claims.get("owner")

    # For JWT users, prefer email, fall back to user_id
    if identity.auth_method == "jwt":
        return identity.email or identity.user_id

    return None


def get_actor_info() -> dict:
    """Get complete actor information for audit logging.

    Returns a dictionary with:
    - actor: The identity string (apikey:name or user:id)
    - actor_owner: The human-readable owner (email or configured owner)
    - auth_method: How the actor was authenticated

    Returns:
        Dictionary with actor information
    """
    identity = get_current_identity()
    if identity is None:
        return {
            "actor": "anonymous",
            "actor_owner": None,
            "auth_method": "none",
        }

    owner = None
    if identity.auth_method == "api_key":
        raw_claims = identity.raw_claims or {}
        owner = raw_claims.get("owner")
    else:
        owner = identity.email or identity.user_id

    return {
        "actor": identity.identity_string,
        "actor_owner": owner,
        "auth_method": identity.auth_method,
    }
