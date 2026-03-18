"""Namespace permission resolution with caching.

Services call check_namespace_permission() to enforce access control.
Permissions are resolved by calling the Registry's /my/namespaces/{prefix}/permission
endpoint, with results cached in-process for 30 seconds.

Usage:
    from wip_auth.permissions import check_namespace_permission

    async def create_document(identity, namespace, ...):
        await check_namespace_permission(identity, namespace, "write")
        # ... proceed with creation
"""

import os
import time
from typing import Literal

from fastapi import HTTPException

from .config import get_auth_config
from .models import UserIdentity

# Cache: "user_id:namespace" → (permission, cached_at)
_grant_cache: dict[str, tuple[str, float]] = {}
GRANT_CACHE_TTL = 30.0  # seconds

# Permission hierarchy
PERMISSION_LEVELS = {"none": 0, "read": 1, "write": 2, "admin": 3}


def _get_registry_url() -> str:
    """Get the Registry service URL for permission checks."""
    return os.getenv(
        "WIP_AUTH_REGISTRY_URL",
        os.getenv("REGISTRY_URL", "http://localhost:8001"),
    )


def _is_superadmin(identity: UserIdentity) -> bool:
    """Check if identity has superadmin access."""
    config = get_auth_config()
    return identity.has_any_group(config.admin_groups)


def permission_sufficient(actual: str, required: str) -> bool:
    """Check if actual permission meets the required level."""
    return PERMISSION_LEVELS.get(actual, 0) >= PERMISSION_LEVELS.get(required, 0)


async def resolve_permission(identity: UserIdentity, namespace: str) -> str:
    """Resolve the effective permission for an identity on a namespace.

    Uses a 30-second in-process cache to avoid hitting the Registry on every request.
    """
    # Superadmin bypass
    if _is_superadmin(identity):
        return "admin"

    cache_key = f"{identity.user_id}:{namespace}"
    now = time.monotonic()

    # Check cache
    if cache_key in _grant_cache:
        permission, cached_at = _grant_cache[cache_key]
        if (now - cached_at) < GRANT_CACHE_TTL:
            return permission

    # Fetch from Registry
    permission = await _fetch_permission_from_registry(identity, namespace)
    _grant_cache[cache_key] = (permission, now)
    return permission


async def _fetch_permission_from_registry(
    identity: UserIdentity, namespace: str
) -> str:
    """Call the Registry's internal check-permission endpoint.

    Uses the service's API key to authenticate the call, and passes
    the user's identity as query parameters so the Registry can resolve
    their grants.
    """
    import httpx

    registry_url = _get_registry_url()
    api_key = os.getenv(
        "WIP_AUTH_LEGACY_API_KEY",
        os.getenv("API_KEY", ""),
    )

    params = {
        "namespace": namespace,
        "user_id": identity.user_id,
        "auth_method": identity.auth_method,
    }
    if identity.email:
        params["email"] = identity.email
    if identity.groups:
        params["groups"] = ",".join(identity.groups)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{registry_url}/api/registry/my/check-permission",
                params=params,
                headers={"X-API-Key": api_key},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("permission", "none")
            return "none"
    except Exception:
        # If Registry is unreachable, deny access (fail closed)
        return "none"


async def check_namespace_permission(
    identity: UserIdentity,
    namespace: str,
    required: Literal["read", "write", "admin"],
) -> None:
    """Check if the identity has the required permission on a namespace.

    Raises HTTP 403 if insufficient permissions.
    Raises HTTP 404 if namespace is invisible (permission == none) to avoid
    leaking namespace names.
    """
    permission = await resolve_permission(identity, namespace)

    if not permission_sufficient(permission, required):
        if permission == "none":
            raise HTTPException(404, "Namespace not found")
        raise HTTPException(
            403,
            f"Requires {required} access to namespace '{namespace}'",
        )


def clear_permission_cache() -> None:
    """Clear the permission cache (for testing)."""
    _grant_cache.clear()
