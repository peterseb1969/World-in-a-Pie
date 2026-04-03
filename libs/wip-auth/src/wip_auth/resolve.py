"""Universal entity ID resolution for WIP.

Resolves human-readable identifiers (e.g., "STATUS", "PATIENT", "STATUS:approved")
and verifies canonical IDs via the Registry's POST /resolve endpoint.

**Every ID goes through Registry.** There is no format-based bypass.
UUIDs are verified via ``entry_id`` lookup; synonyms are resolved via
``composite_key`` hash lookup. A TTL cache (5 minutes) minimises latency —
the second time any ID is seen, resolution is a local dict lookup.

Usage:
    from wip_auth.resolve import resolve_entity_id, resolve_entity_ids

    # Single resolution (synonym or canonical ID)
    canonical_id = await resolve_entity_id("STATUS", "terminology", "wip")

    # Batch resolution
    id_map = await resolve_entity_ids(["STATUS", "GENDER"], "terminology", "wip")
"""

import logging
import os
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# UUID pattern: 8-4-4-4-12 hex chars (covers UUID4, UUID7, and similar)
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# TTL cache: {cache_key: (canonical_id, expire_time)}
_resolution_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 300  # 5 minutes

# Transport injection for in-process testing.
# When set, httpx.AsyncClient uses this transport instead of real HTTP.
_resolve_transport: Any | None = None


def set_resolve_transport(transport: Any | None) -> None:
    """Set the httpx transport for resolve calls (for in-process testing)."""
    global _resolve_transport
    _resolve_transport = transport


def _get_registry_url() -> str:
    """Get Registry URL from environment."""
    return os.getenv(
        "WIP_AUTH_REGISTRY_URL",
        os.getenv("REGISTRY_URL", "http://localhost:8001"),
    )


def _get_api_key() -> str:
    """Get API key for Registry calls."""
    return os.getenv(
        "REGISTRY_API_KEY",
        os.getenv("API_KEY", "dev_master_key_for_testing"),
    )


def _looks_like_uuid(raw_id: str) -> bool:
    """Check if a string looks like a UUID.

    Used only to decide which field to send to Registry's /resolve endpoint:
    UUID-shaped strings go as ``entry_id`` (direct lookup), everything else
    goes as ``composite_key`` (synonym resolution). Both paths verify against
    Registry — this is NOT a bypass.
    """
    return bool(_UUID_PATTERN.match(raw_id))



def _build_composite_key(
    raw_id: str,
    entity_type: str,
    namespace: str,
) -> dict[str, Any]:
    """Build a composite key from a synonym string and context.

    Resolution is deterministic:
    - Bare values resolve in the caller's own namespace.
    - Cross-namespace requires explicit prefix (e.g., ``wip:STATUS``).

    Term references use colon notation with 2 or 3 parts:
    - ``TERMINOLOGY:VALUE`` — own namespace
    - ``NS:TERMINOLOGY:VALUE`` — cross-namespace

    Other entity types:
    - ``VALUE`` — own namespace
    - ``NS:VALUE`` — cross-namespace

    Args:
        raw_id: The human-readable identifier
        entity_type: Singular entity type (terminology, term, template, document)
        namespace: Caller's namespace (used for bare values)
    """
    if entity_type == "term":
        parts = raw_id.split(":", 2)
        if len(parts) == 3:
            # NS:TERMINOLOGY:VALUE — cross-namespace term
            return {
                "ns": parts[0],
                "type": "term",
                "terminology": parts[1],
                "value": parts[2],
            }
        elif len(parts) == 2:
            # TERMINOLOGY:VALUE — own namespace
            return {
                "ns": namespace,
                "type": "term",
                "terminology": parts[0],
                "value": parts[1],
            }
        else:
            return {"ns": namespace, "type": "term", "value": raw_id}
    else:
        if ":" in raw_id:
            # NS:VALUE — cross-namespace
            ns_prefix, value = raw_id.split(":", 1)
            return {"ns": ns_prefix, "type": entity_type, "value": value}
        # Bare value — own namespace
        return {"ns": namespace, "type": entity_type, "value": raw_id}


def _build_resolve_payload(
    raw_id: str,
    entity_type: str,
    namespace: str,
    include_statuses: list[str] | None = None,
) -> dict[str, Any]:
    """Build a single item for the /resolve request payload.

    UUID-shaped IDs are sent as ``entry_id`` for direct verification.
    Everything else is sent as ``composite_key`` for synonym resolution.
    """
    payload: dict[str, Any] = {}
    if _looks_like_uuid(raw_id):
        payload["entry_id"] = raw_id
    else:
        payload["composite_key"] = _build_composite_key(raw_id, entity_type, namespace)
    if include_statuses:
        payload["include_statuses"] = include_statuses
    return payload


def _get_cached(cache_key: str) -> str | None:
    """Get a cached resolution result if still valid."""
    entry = _resolution_cache.get(cache_key)
    if entry is None:
        return None
    canonical_id, expire_time = entry
    if time.monotonic() > expire_time:
        del _resolution_cache[cache_key]
        return None
    return canonical_id


def _set_cached(cache_key: str, canonical_id: str) -> None:
    """Cache a resolution result."""
    _resolution_cache[cache_key] = (canonical_id, time.monotonic() + _CACHE_TTL)


def clear_resolution_cache() -> None:
    """Clear the resolution cache (useful for testing)."""
    _resolution_cache.clear()


class EntityNotFoundError(Exception):
    """Raised when synonym resolution finds no matching entity."""

    def __init__(self, identifier: str, entity_type: str):
        self.identifier = identifier
        self.entity_type = entity_type
        super().__init__(f"No {entity_type} found for identifier: {identifier}")


async def resolve_entity_id(
    raw_id: str,
    entity_type: str,
    namespace: str,
    include_statuses: list[str] | None = None,
) -> str:
    """Resolve any identifier to its canonical ID via Registry.

    Both synonyms and canonical IDs are verified against Registry.
    Results are cached for 5 minutes.

    Args:
        raw_id: The identifier (canonical ID or human-readable synonym)
        entity_type: Singular entity type: terminology, term, template, document
        namespace: Current namespace for resolution context

    Returns:
        The canonical entity ID

    Raises:
        EntityNotFoundError: If the identifier is not found in Registry
    """
    cache_key = f"{namespace}:{entity_type}:{raw_id}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    payload = _build_resolve_payload(raw_id, entity_type, namespace, include_statuses)

    registry_url = _get_registry_url()
    api_key = _get_api_key()

    try:
        client_kwargs: dict[str, Any] = {"timeout": 10.0}
        if _resolve_transport is not None:
            client_kwargs["transport"] = _resolve_transport
            client_kwargs["base_url"] = registry_url
        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.post(
                f"{registry_url}/api/registry/entries/resolve",
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                json=[payload],
            )
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as e:
        logger.debug("Registry unreachable for resolution: %s", e)
        raise EntityNotFoundError(raw_id, entity_type) from e

    if response.status_code != 200:
        logger.warning(
            "Registry resolve failed for %s (%s): %s",
            raw_id, entity_type, response.status_code,
        )
        raise EntityNotFoundError(raw_id, entity_type)

    data = response.json()
    results = data.get("results", [])
    if results and results[0].get("status") == "found":
        canonical_id = results[0]["entry_id"]
        _set_cached(cache_key, canonical_id)
        return canonical_id

    raise EntityNotFoundError(raw_id, entity_type)


async def resolve_entity_ids(
    raw_ids: list[str],
    entity_type: str,
    namespace: str,
    include_statuses: list[str] | None = None,
) -> dict[str, str]:
    """Batch resolve multiple identifiers via Registry.

    Both synonyms and canonical IDs are verified. Cached results are
    used where available; uncached IDs are resolved in a single batch call.

    Args:
        raw_ids: List of identifiers (mix of canonical and synonym)
        entity_type: Singular entity type
        namespace: Current namespace

    Returns:
        Dict mapping each raw_id to its canonical ID

    Raises:
        EntityNotFoundError: If any identifier is not found
    """
    result: dict[str, str] = {}
    to_resolve: list[str] = []

    for raw_id in raw_ids:
        cache_key = f"{namespace}:{entity_type}:{raw_id}"
        cached = _get_cached(cache_key)
        if cached:
            result[raw_id] = cached
        else:
            to_resolve.append(raw_id)

    if not to_resolve:
        return result

    # Build payloads — UUIDs get entry_id, synonyms get composite_key
    payloads = [
        _build_resolve_payload(rid, entity_type, namespace, include_statuses)
        for rid in to_resolve
    ]

    registry_url = _get_registry_url()
    api_key = _get_api_key()

    try:
        client_kwargs: dict[str, Any] = {"timeout": 10.0}
        if _resolve_transport is not None:
            client_kwargs["transport"] = _resolve_transport
            client_kwargs["base_url"] = registry_url
        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.post(
                f"{registry_url}/api/registry/entries/resolve",
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                json=payloads,
            )
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as e:
        logger.debug("Registry unreachable for batch resolution: %s", e)
        raise EntityNotFoundError(to_resolve[0], entity_type) from e

    if response.status_code != 200:
        logger.warning("Registry batch resolve failed: %s", response.status_code)
        raise EntityNotFoundError(to_resolve[0], entity_type)

    data = response.json()
    for i, resolve_result in enumerate(data.get("results", [])):
        raw_id = to_resolve[i]
        if resolve_result.get("status") == "found":
            canonical_id = resolve_result["entry_id"]
            cache_key = f"{namespace}:{entity_type}:{raw_id}"
            _set_cached(cache_key, canonical_id)
            result[raw_id] = canonical_id
        else:
            raise EntityNotFoundError(raw_id, entity_type)

    return result
