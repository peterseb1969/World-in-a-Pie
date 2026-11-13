"""Namespace permission resolution with caching.

Services call check_namespace_permission() to enforce access control.
Permissions are the Registry's answers, asked of its service-to-service
endpoints /api/registry/my/check-permission (one namespace) and
/api/registry/my/accessible-namespaces (the readable list), with definitive
answers cached in-process for 30 seconds.

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

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

from cachetools import TTLCache
from fastapi import HTTPException

from .config import get_auth_config
from .models import UserIdentity

logger = logging.getLogger(__name__)

# TTL caches. cachetools.TTLCache holds no lock and nothing here takes one:
# they are safe on one asyncio event loop, not across threads.
GRANT_CACHE_TTL = 30.0  # seconds
_grant_cache: TTLCache = TTLCache(maxsize=1024, ttl=GRANT_CACHE_TTL)
_accessible_cache: TTLCache = TTLCache(maxsize=256, ttl=GRANT_CACHE_TTL)

# Permission hierarchy
PERMISSION_LEVELS = {"none": 0, "read": 1, "write": 2, "admin": 3}

# The Registry gave no answer (unreachable, timed out, or any status but a
# well-formed 200). Not a permission level — permission_sufficient treats it
# as nothing, so every check still fails closed — and never cached, so
# access returns as soon as the Registry does instead of staying denied for
# the cache lifetime.
PERMISSION_UNAVAILABLE = "unavailable"

_UNAVAILABLE_DETAIL = "Authorization service unavailable — the Registry could not be reached; retry shortly"


def _get_registry_url() -> str:
    """Get the Registry service URL for permission checks."""
    return os.getenv(
        "WIP_AUTH_REGISTRY_URL",
        os.getenv("REGISTRY_URL", "http://localhost:8001"),
    )


def _service_api_key() -> str:
    """The key this service presents to the Registry's permission doors.

    WIP_AUTH_LEGACY_API_KEY first (wip-auth copies MASTER_API_KEY or API_KEY
    into it at import when it is unset), then API_KEY. An empty value counts
    as unset: an empty key is no credential, and reading it as one sent every
    check with an empty X-API-Key, which the Registry answers 401 and every
    caller then reported as the Registry being unavailable (503). Blanking
    the variable to switch the legacy key record off must not do that.
    """
    return os.getenv("WIP_AUTH_LEGACY_API_KEY") or os.getenv("API_KEY") or ""


def is_superadmin(identity: UserIdentity) -> bool:
    """Whether the identity is a platform administrator (in ADMIN_GROUP).

    For platform-level gates only (system endpoints, URL imports, cross-
    namespace file listings). A permission on a namespace's data is never
    decided by it: that is the Registry's answer, which refuses even an
    administrator a namespace locked for deletion."""
    config = get_auth_config()
    return identity.has_any_group(config.admin_groups)


def permission_sufficient(actual: str, required: str) -> bool:
    """Check if actual permission meets the required level."""
    return PERMISSION_LEVELS.get(actual, 0) >= PERMISSION_LEVELS.get(required, 0)


def _key_scope(identity: UserIdentity) -> list[str] | None:
    """An api key's namespace scope, or None (unscoped, or not a key).

    Only a key's record carries a scope; another identity's ``namespaces``
    claim (an OIDC token may carry any claim) is not one. A scope that is not
    a list of strings is not forwarded, so the Registry answers the key as
    unscoped, which gives a non-privileged key nothing.
    """
    if identity.auth_method != "api_key":
        return None
    scope = (identity.raw_claims or {}).get("namespaces")
    if not isinstance(scope, list) or not all(isinstance(ns, str) for ns in scope):
        return None
    return scope


def _registry_question(
    identity: UserIdentity, namespace: str | None = None
) -> tuple[dict[str, str], dict[str, str]]:
    """The query parameters and identity headers a permission lookup sends to
    the Registry: everything its answer depends on, apart from the service
    key that authenticates the call."""
    params = {"user_id": identity.user_id, "auth_method": identity.auth_method}
    if namespace is not None:
        params["namespace"] = namespace
    if identity.email:
        params["email"] = identity.email
    # Forward username so api_key grant subjects match the same spelling
    # (bare key name) on this path as on direct Registry calls; without it
    # the Registry's synthetic identity falls back to username=user_id
    # ("apikey:<name>") and grants stored under the key name never match.
    if identity.username:
        params["username"] = identity.username
    # Groups and key scope travel as headers, keeping group names out of
    # access logs. The scope lets the Registry's synthetic identity see the
    # same scoping the calling service does; without it every api key would
    # look unscoped and a non-privileged key would get "none".
    headers: dict[str, str] = {}
    if identity.groups:
        headers["X-User-Groups"] = ",".join(identity.groups)
    scope = _key_scope(identity)
    if scope is not None:
        headers["X-Key-Namespaces"] = ",".join(scope)
    return params, headers


def _cache_key(identity: UserIdentity, namespace: str | None = None) -> tuple:
    """The cache key of a lookup: the whole question sent to the Registry.

    The answer depends on the caller's groups and key scope as well as its
    user_id, so two identities sharing a user_id (one user arriving with
    different groups, a key name whose scope changed) never share an answer.
    Group and scope order do not change the answer, so they are sorted.
    """
    params, _ = _registry_question(identity, namespace)
    scope = _key_scope(identity)
    return (
        tuple(sorted(params.items())),
        tuple(sorted(identity.groups or [])),
        None if scope is None else tuple(sorted(scope)),
    )


async def resolve_permission(identity: UserIdentity, namespace: str) -> str:
    """Resolve the effective permission for an identity on a namespace.

    Always the Registry's answer, a platform administrator's included: the
    Registry gives wip-admins admin on every namespace except one locked
    for deletion, which is closed to everyone. A service answering admins
    itself would keep that namespace's data open to them while the Registry
    deletes it. Uses a 30-second in-process cache to avoid hitting the
    Registry on every request; the Registry drops it when it locks a
    namespace.
    """
    cache_key = _cache_key(identity, namespace)

    # Check cache (TTLCache handles expiry automatically)
    cached: str | None = _grant_cache.get(cache_key)
    if cached is not None:
        return cached

    # Fetch from Registry; cache only a definitive answer.
    permission = await _fetch_permission_from_registry(identity, namespace)
    if permission != PERMISSION_UNAVAILABLE:
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
    api_key = _service_api_key()

    params, identity_headers = _registry_question(identity, namespace)
    headers = {"X-API-Key": api_key, **identity_headers}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{registry_url}/api/registry/my/check-permission",
                params=params,
                headers=headers,
            )
            # The Registry answers every definite permission, "none"
            # included, as a 200. Any other status (a 401 for a stale
            # service key, a 403 for a key that is not a service account,
            # a 5xx) and a 200 without a permission string are no answer:
            # uncached, and the caller answers 503 rather than a false "none".
            if resp.status_code != 200:
                logger.warning(
                    "Registry check-permission answered HTTP %s for %s on %s",
                    resp.status_code, identity.user_id, namespace,
                )
                return PERMISSION_UNAVAILABLE
            permission = resp.json().get("permission")
            if not isinstance(permission, str):
                return PERMISSION_UNAVAILABLE
            return permission
    except Exception:
        # Registry unreachable: no answer, not a "none" answer. Callers
        # still deny (fail closed), but nothing caches it.
        return PERMISSION_UNAVAILABLE


async def check_namespace_permission(
    identity: UserIdentity,
    namespace: str,
    required: Literal["read", "write", "admin"],
) -> None:
    """Check if the identity has the required permission on a namespace.

    Raises HTTP 403 if insufficient permissions.
    Raises HTTP 404 if namespace is invisible (permission == none) to avoid
    leaking namespace names.
    Raises HTTP 503 if the Registry could not be asked — an outage must not
    look like a missing namespace.
    """
    permission = await resolve_permission(identity, namespace)

    if not permission_sufficient(permission, required):
        if permission == PERMISSION_UNAVAILABLE:
            raise HTTPException(503, _UNAVAILABLE_DETAIL)
        if permission == "none":
            raise HTTPException(404, "Namespace not found")
        raise HTTPException(
            403,
            f"Requires {required} access to namespace '{namespace}'",
        )


async def can_read(identity: UserIdentity, namespace: str) -> bool:
    """Whether ``identity`` may read ``namespace``.

    For doors addressed by an entity's id: False means the namespace is
    invisible to the caller, and the door must then answer exactly as it
    does for an id that names nothing — same status, same text, same body
    shape (a 404, an empty page, a not_found item). Any difference would
    confirm that the entity exists, and where, which the namespace 404
    exists to hide. Raises HTTP 503 when the Registry could not be asked.
    """
    permission = await resolve_permission(identity, namespace)
    if permission == PERMISSION_UNAVAILABLE:
        raise HTTPException(503, _UNAVAILABLE_DETAIL)
    return permission_sufficient(permission, "read")


async def check_readable(identity: UserIdentity, namespace: str, not_found: str) -> None:
    """Read on the namespace of an entity the caller asked for by id, for a
    door whose miss is a 404.

    An entity in a namespace the caller cannot see answers exactly as an id
    that names nothing (``not_found``), not "Namespace not found": the
    different text would confirm that the id exists, and where. A door whose
    miss is not a 404 (an empty page, a not_found item) uses ``can_read`` and
    answers its own miss. A namespace the caller names itself is checked
    with check_namespace_permission.
    """
    await check_entity_permission(identity, namespace, "read", not_found)


async def check_entity_permission(
    identity: UserIdentity,
    namespace: str,
    required: Literal["read", "write", "admin"],
    not_found: str,
) -> None:
    """``required`` on the namespace of an entity the caller asked for by
    id, for a door whose miss is a 404 with the text ``not_found``.

    A caller who cannot read the namespace gets that 404, exactly as for an
    id that names nothing; one who can read but lacks ``required`` gets the
    403 (the entity is visible to it, so nothing is revealed).
    """
    if not await can_read(identity, namespace):
        raise HTTPException(status_code=404, detail=not_found)
    await check_namespace_permission(identity, namespace, required)


async def entity_write_refusals(
    identity: UserIdentity, id_to_namespace: Mapping[str, str], not_found: str,
) -> dict[str, tuple[str, str]]:
    """``write_refusals`` keyed by entity id: why ``identity`` may not write
    each entity of a bulk request, as (error, error_code). An id absent from
    the result may be written (or names nothing, which the door reports)."""
    by_namespace = await write_refusals(identity, set(id_to_namespace.values()), not_found)
    return {
        entity_id: by_namespace[ns]
        for entity_id, ns in id_to_namespace.items() if ns in by_namespace
    }


async def write_refusals(
    identity: UserIdentity, namespaces: set[str], not_found: str
) -> dict[str, tuple[str, str]]:
    """Per namespace, why ``identity`` may not write there, as (error,
    error_code); a namespace absent from the result may be written.

    For bulk writes: asked once per namespace and before any item of the batch
    is written, so a refusal is reported on its items while the rest proceed,
    and an authorization outage (503) refuses the whole request before any
    item has changed. A namespace the caller cannot see answers ``not_found``
    with the text of an id that names nothing, as check_readable does, so the
    response does not confirm that the entity exists, or where.
    """
    refusals: dict[str, tuple[str, str]] = {}
    for ns in sorted(namespaces):
        try:
            await check_namespace_permission(identity, ns, "write")
        except HTTPException as exc:
            if exc.status_code == 403:
                refusals[ns] = (str(exc.detail), "forbidden")
            elif exc.status_code == 404:
                refusals[ns] = (not_found, "not_found")
            else:
                raise
    return refusals


@dataclass
class NamespaceFilter:
    """MongoDB namespace filter resolved from identity and optional namespace param.

    Merge `query` into your MongoDB query dict. It always has one shape,
    {"namespace": {"$in": [...]}}, an administrator's included: there is no
    unrestricted filter, because a namespace locked for deletion is closed to
    everyone and must drop out of every listing.

    Single-namespace is {"$in": ["ns1"]} — MongoDB optimizes this identically
    to {"namespace": "ns1"}, so callers never need to special-case it.
    """

    query: dict = field(default_factory=dict)
    namespaces: list[str] = field(default_factory=list)


async def resolve_namespace_filter(
    identity: UserIdentity,
    namespace: str | None,
    required: Literal["read", "write", "admin"] = "read",
) -> NamespaceFilter:
    """Resolve namespace into a MongoDB query filter.

    If namespace is provided: check permission, return single-namespace filter.
    If namespace is None: return the namespaces where the caller holds at least
    `required`. For "read" that is every accessible namespace; for "write" or
    "admin" each accessible namespace's permission is checked, so a caller who
    omits the namespace never gets a weaker check than one who names it.

    Returns a NamespaceFilter with:
      - query: dict to merge into MongoDB query
      - namespaces: the resolved list (for logging/debugging)

    Raises:
      HTTPException 404: if explicit namespace is invisible (permission == none)
      HTTPException 403: if explicit namespace has insufficient permission,
                         or if identity has no accessible namespaces, or
                         (no namespace given) none holds `required`
      HTTPException 503: if the Registry could not be asked
    """
    if namespace:
        await check_namespace_permission(identity, namespace, required)
        return NamespaceFilter(
            query={"namespace": {"$in": [namespace]}},
            namespaces=[namespace],
        )

    accessible = await resolve_accessible_namespaces(identity)
    if not accessible:
        raise HTTPException(403, "No accessible namespaces")
    if required != "read":
        # Accessible means visible at any level. A write or admin filter keeps
        # only the namespaces that meet the requirement; an outage on any of
        # them is no answer, not a smaller list.
        permitted = []
        for ns in accessible:
            permission = await resolve_permission(identity, ns)
            if permission == PERMISSION_UNAVAILABLE:
                raise HTTPException(503, _UNAVAILABLE_DETAIL)
            if permission_sufficient(permission, required):
                permitted.append(ns)
        if not permitted:
            raise HTTPException(403, f"Requires {required} access to a namespace")
        accessible = permitted
    return NamespaceFilter(
        query={"namespace": {"$in": accessible}},
        namespaces=accessible,
    )


async def resolve_accessible_namespaces(identity: UserIdentity) -> list[str]:
    """The prefixes of the namespaces an identity can read: the Registry's
    answer, cached in-process for 30 seconds.

    Always a list, a platform administrator's included: the Registry answers
    an administrator with every namespace except one locked for deletion,
    which is closed to everyone. A listing that names no namespace filters
    by this list, so a locked namespace's data drops out of it for every
    caller. Whether a caller is an administrator is is_superadmin's
    question, never this list's.

    Raises HTTP 503 if the Registry could not be asked, rather than treating
    the outage as "no namespaces" (which would read as 403 or empty results).
    """
    cache_key = _cache_key(identity)

    cached: list[str] | None = _accessible_cache.get(cache_key)
    if cached is not None:
        return cached

    namespaces = await _fetch_accessible_from_registry(identity)
    if namespaces is _UNAVAILABLE_LIST:
        raise HTTPException(503, _UNAVAILABLE_DETAIL)
    _accessible_cache[cache_key] = namespaces
    return namespaces


# Returned by _fetch_accessible_from_registry when the Registry could not be
# asked; distinct from [] (a definitive "no namespaces").
_UNAVAILABLE_LIST: list[str] = []


async def _fetch_accessible_from_registry(identity: UserIdentity) -> list[str]:
    """Call Registry's internal accessible-namespaces endpoint."""
    import httpx

    registry_url = _get_registry_url()
    api_key = _service_api_key()

    params, identity_headers = _registry_question(identity)
    headers = {"X-API-Key": api_key, **identity_headers}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{registry_url}/api/registry/my/accessible-namespaces",
                params=params,
                headers=headers,
            )
            # As for check-permission: only a well-formed 200 is an answer.
            if resp.status_code != 200:
                logger.warning(
                    "Registry accessible-namespaces answered HTTP %s for %s",
                    resp.status_code, identity.user_id,
                )
                return _UNAVAILABLE_LIST
            namespaces = resp.json().get("namespaces")
            if not isinstance(namespaces, list):
                return _UNAVAILABLE_LIST
            return cast(list[str], namespaces)
    except Exception:
        return _UNAVAILABLE_LIST


def clear_permission_cache() -> None:
    """Clear the permission cache (for testing)."""
    _grant_cache.clear()
    _accessible_cache.clear()
