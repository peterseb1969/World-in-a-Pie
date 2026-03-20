"""FastAPI dependencies for authentication.

These dependencies can be used in route handlers to require authentication
and authorization. They integrate with the auth middleware to access
the current identity.

Example usage:
    from wip_auth import require_identity, require_groups

    @app.get("/protected")
    async def protected_route(identity: UserIdentity = Depends(require_identity())):
        return {"user": identity.username}

    @app.get("/admin")
    async def admin_route(identity: UserIdentity = Depends(require_admin())):
        return {"admin": identity.username}
"""

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request

from .identity import get_current_identity
from .models import UserIdentity


def require_identity() -> Callable[[Request], Awaitable[UserIdentity]]:
    """Require any authenticated identity.

    Returns a FastAPI dependency function that extracts the current identity
    and raises 401 if not authenticated.

    Returns:
        Dependency function for use with FastAPI's Depends()

    Example:
        @app.get("/me")
        async def get_me(identity: UserIdentity = Depends(require_identity())):
            return {"user_id": identity.user_id}
    """
    async def dependency(request: Request) -> UserIdentity:
        identity = get_current_identity()
        if identity is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer, ApiKey"},
            )
        return identity

    return dependency


def require_groups(
    groups: list[str], require_all: bool = False
) -> Callable[[Request], Awaitable[UserIdentity]]:
    """Require identity with specified groups.

    Args:
        groups: List of group names to require
        require_all: If True, require all groups. If False (default), require any.

    Returns:
        Dependency function for use with FastAPI's Depends()

    Example:
        @app.get("/editors")
        async def editors_only(
            identity: UserIdentity = Depends(require_groups(["wip-editors", "wip-admins"]))
        ):
            return {"user": identity.username}
    """
    async def dependency(request: Request) -> UserIdentity:
        identity = get_current_identity()
        if identity is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer, ApiKey"},
            )

        if require_all:
            has_access = identity.has_all_groups(groups)
        else:
            has_access = identity.has_any_group(groups)

        if not has_access:
            raise HTTPException(
                status_code=403,
                detail=f"Required groups: {groups}",
            )

        return identity

    return dependency


def require_admin() -> Callable[[Request], Awaitable[UserIdentity]]:
    """Require admin-level access.

    Shortcut for require_groups(["wip-admins"]).
    The admin group names can be configured in AuthConfig.

    Returns:
        Dependency function for use with FastAPI's Depends()
    """
    from .config import get_auth_config

    config = get_auth_config()
    return require_groups(config.admin_groups)


def optional_identity() -> Callable[[Request], Awaitable[UserIdentity | None]]:
    """Get the current identity if authenticated, None otherwise.

    Unlike require_identity(), this does not raise an error for
    unauthenticated requests.

    Returns:
        Dependency function for use with FastAPI's Depends()

    Example:
        @app.get("/public")
        async def public_route(
            identity: UserIdentity | None = Depends(optional_identity())
        ):
            if identity:
                return {"greeting": f"Hello, {identity.username}"}
            return {"greeting": "Hello, anonymous"}
    """
    async def dependency(request: Request) -> UserIdentity | None:
        return get_current_identity()

    return dependency


# Convenience aliases for cleaner imports
RequireIdentity = require_identity
RequireGroups = require_groups
RequireAdmin = require_admin
OptionalIdentity = optional_identity


async def require_namespace_read(
    identity: UserIdentity, namespace: str
) -> None:
    """Require read (or higher) access to a namespace.

    Call from route handlers after extracting namespace from the request.

    Args:
        identity: The authenticated identity.
        namespace: The namespace to check access for.

    Raises:
        HTTPException 404 if namespace is invisible (no access).
        HTTPException 403 if insufficient permissions.
    """
    from .permissions import check_namespace_permission

    await check_namespace_permission(identity, namespace, "read")


async def require_namespace_write(
    identity: UserIdentity, namespace: str
) -> None:
    """Require write (or higher) access to a namespace."""
    from .permissions import check_namespace_permission

    await check_namespace_permission(identity, namespace, "write")


async def require_namespace_admin(
    identity: UserIdentity, namespace: str
) -> None:
    """Require admin access to a namespace."""
    from .permissions import check_namespace_permission

    await check_namespace_permission(identity, namespace, "admin")


# Legacy compatibility: direct dependency function for require_api_key
# This matches the old signature where Depends(require_api_key) was used
async def require_api_key(request: Request) -> UserIdentity:
    """Legacy compatibility wrapper for authentication.

    This function provides backward compatibility with existing code that
    uses Depends(require_api_key). It checks authentication and returns
    the UserIdentity (which can be used where str was expected).

    Args:
        request: The FastAPI request

    Returns:
        UserIdentity if authenticated

    Raises:
        HTTPException: If not authenticated
    """
    identity = get_current_identity()
    if identity is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer, ApiKey"},
        )
    return identity
