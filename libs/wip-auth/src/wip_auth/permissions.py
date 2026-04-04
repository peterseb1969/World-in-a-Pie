"""Namespace permission resolution with caching.

Services call check_namespace_permission() to enforce access control.
Permissions are resolved by calling the Registry's /my/namespaces/{prefix}/permission
endpoint, with results cached in-process for 30 seconds.

Usage:
    from wip_auth.permissions import check_namespace_permission, resolve_namespace_filter

    async def create_document(identity, namespace, ...):
        await check_namespace_permission(identity, namespace, "write")
        # ... proceed with creation

    async def list_items(identity, namespace=None, ...):
        ns_filter = await resolve_namespace_filter(identity, namespace)
        query = {"status": "active"}
        query.update(ns_filter.query)
        # ... use query for MongoDB
"""

import os
from dataclasses import dataclass, field
from typing import Literal

from cachetools import TTLCache
from fastapi import HTTPException

from .config import get_auth_config
from .models import UserIdentity

# Thread-safe TTL caches (L1 — prevents potential race conditions)
GRANT_CACHE_TTL = 30.0  # seconds
_grant_cache: TTLCache = TTLCache(maxsize=1024, ttl=GRANT_CACHE_TTL)
_accessible_cache: TTLCache = TTLCache(maxsize=256, ttl=GRANT_CACHE_TTL)

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

    # Check cache (TTLCache handles expiry automatically)
    cached: str | None = _grant_cache.get(cache_key)
    if cached is not None:
        return cached

    # Fetch from Registry
    permission = await _fetch_permission_from_registry(identity, namespace)
    _grant_cache[cache_key] = permission
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

    # Pass groups in header (M2 — avoid leaking group names in access logs/caches)
    headers = {"X-API-Key": api_key}
    if identity.groups:
        headers["X-User-Groups"] = ",".join(identity.groups)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{registry_url}/api/registry/my/check-permission",
                params=params,
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                permission: str = data.get("permission", "none")
                return permission
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


@dataclass
class NamespaceFilter:
    """MongoDB namespace filter resolved from identity and optional namespace param.

    Merge `query` into your MongoDB query dict. Two shapes only:
    - {} — superadmin, no namespace restriction
    - {"namespace": {"$in": [...]}} — scoped to specific namespaces

    Single-namespace is {"$in": ["ns1"]} — MongoDB optimizes this identically
    to {"namespace": "ns1"}, so callers never need to special-case it.
    """

    query: dict = field(default_factory=dict)
    namespaces: list[str] | None = None  # None = superadmin (all)


async def resolve_namespace_filter(
    identity: UserIdentity,
    namespace: str | None,
    required: Literal["read", "write", "admin"] = "read",
) -> NamespaceFilter:
    """Resolve namespace into a MongoDB query filter.

    If namespace is provided: check permission, return single-namespace filter.
    If namespace is None: resolve accessible namespaces, return multi-namespace filter.

    Returns a NamespaceFilter with:
      - query: dict to merge into MongoDB query
      - namespaces: the resolved list (for logging/debugging), or None for superadmin

    Raises:
      HTTPException 404: if explicit namespace is invisible (permission == none)
      HTTPException 403: if explicit namespace has insufficient permission,
                         or if identity has no accessible namespaces
    """
    if namespace:
        await check_namespace_permission(identity, namespace, required)
        return NamespaceFilter(
            query={"namespace": {"$in": [namespace]}},
            namespaces=[namespace],
        )

    accessible = await resolve_accessible_namespaces(identity)
    if accessible is None:
        # Superadmin: no filter
        return NamespaceFilter(query={}, namespaces=None)
    if not accessible:
        raise HTTPException(403, "No accessible namespaces")
    return NamespaceFilter(
        query={"namespace": {"$in": accessible}},
        namespaces=accessible,
    )


async def resolve_accessible_namespaces(identity: UserIdentity) -> list[str] | None:
    """Resolve which namespaces an identity can access.

    Returns None for superadmin (meaning: no filter needed, all namespaces).
    Returns a list of namespace prefixes for regular users.
    Uses a 30-second in-process cache.
    """
    if _is_superadmin(identity):
        return None

    cache_key = identity.user_id

    # Use sentinel to distinguish "not cached" from "cached None"
    cached: list[str] | None | object = _accessible_cache.get(cache_key, _SENTINEL)
    if cached is not _SENTINEL:
        return cached  # type: ignore[return-value]

    namespaces = await _fetch_accessible_from_registry(identity)
    _accessible_cache[cache_key] = namespaces
    return namespaces


_SENTINEL = object()


async def _fetch_accessible_from_registry(identity: UserIdentity) -> list[str] | None:
    """Call Registry's internal accessible-namespaces endpoint."""
    import httpx

    registry_url = _get_registry_url()
    api_key = os.getenv(
        "WIP_AUTH_LEGACY_API_KEY",
        os.getenv("API_KEY", ""),
    )

    params = {
        "user_id": identity.user_id,
        "auth_method": identity.auth_method,
    }
    if identity.email:
        params["email"] = identity.email

    headers = {"X-API-Key": api_key}
    if identity.groups:
        headers["X-User-Groups"] = ",".join(identity.groups)

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{registry_url}/api/registry/my/accessible-namespaces",
                params=params,
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("is_superadmin"):
                    return None
                namespaces: list[str] = data.get("namespaces", [])
                return namespaces
            return []
    except Exception:
        return []


def clear_permission_cache() -> None:
    """Clear the permission cache (for testing)."""
    _grant_cache.clear()
    _accessible_cache.clear()
