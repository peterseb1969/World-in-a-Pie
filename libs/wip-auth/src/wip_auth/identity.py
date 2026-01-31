"""Identity context management.

Provides a request-scoped context for storing the current authenticated identity.
This allows middleware to set the identity and dependencies to access it.
"""

from contextvars import ContextVar
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


def set_current_identity(identity: "UserIdentity | None") -> None:
    """Set the authenticated identity for the current request.

    This should only be called by the auth middleware.

    Args:
        identity: The authenticated identity or None
    """
    _current_identity.set(identity)


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
