"""Universal entity ID resolution for WIP.

Resolves human-readable identifiers (e.g., "STATUS", "PATIENT",
"wip:STATUS:approved") and verifies canonical IDs via the Registry's
POST /resolve endpoint. Term identifiers are strict: the fully qualified
3-part form or the structured field form (resolve_term_by_fields) — the
2-part "TERMINOLOGY:VALUE" shorthand is rejected at the API-door helpers
in fastapi_helpers, because a value containing ':' is indistinguishable
from it.

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

import asyncio
import contextvars
import logging
import os
import re
import time
from typing import Any, cast

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

# True while handling a request that may write (any method but GET, HEAD,
# OPTIONS); set per request by WriteRequestMiddleware, which setup_auth
# installs in every service. Writes must hit the Registry: a cached mapping
# can outlive the entity it names by up to _CACHE_TTL (a namespace deleted
# and re-created under the same name keeps resolving to the deleted ids), so
# answering a write from it acts on — or persists — an id that no longer
# exists. Enforced here rather than left to each route's bypass_cache flag,
# which routes forgot. Outside a request (workers) the cache is used as
# before.
_write_request: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "wip_write_request", default=False
)


def _cache_readable(bypass_cache: bool) -> bool:
    """Whether a resolution may be answered from the cache."""
    return not (bypass_cache or _write_request.get())


class WriteRequestMiddleware:
    """Marks the request as a write for resolution (see _write_request).

    Pure ASGI, so the flag is set in the context the rest of the stack —
    including BaseHTTPMiddleware's handler tasks — inherits.
    """

    _READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        token = _write_request.set(scope.get("method", "GET") not in self._READ_METHODS)
        try:
            await self.app(scope, receive, send)
        finally:
            _write_request.reset(token)

# Transport injection for in-process testing.
# When set, httpx.AsyncClient uses this transport instead of real HTTP.
_resolve_transport: Any | None = None


def set_resolve_transport(transport: Any | None) -> None:
    """Set the httpx transport for resolve calls (for in-process testing)."""
    global _resolve_transport
    _resolve_transport = transport


# One pooled client per (event loop, injected transport) instead of a fresh
# AsyncClient per resolve call. A fresh client per call means a new
# connection (and potentially a DNS lookup) per resolved id — on bulk write
# doors that was the dominant serial cost (a 200-relation request opened
# 400 connections to Registry). The pool is rebuilt when the running loop
# changes (each pytest-asyncio test runs its own loop), when the injected
# transport changes (set_resolve_transport in test setups/teardowns), or
# when the client was closed. A client abandoned on a dead loop cannot be
# awaited closed from the new loop; it is dropped and its sockets are
# reclaimed at GC — acceptable for a per-process singleton.
_pooled_client: httpx.AsyncClient | None = None
_pooled_client_loop: Any | None = None
_pooled_client_transport: Any | None = None


def _get_resolve_client() -> httpx.AsyncClient:
    """The shared client for all Registry resolve/lookup calls."""
    global _pooled_client, _pooled_client_loop, _pooled_client_transport
    loop = asyncio.get_running_loop()
    if (
        _pooled_client is None
        or _pooled_client.is_closed
        or _pooled_client_loop is not loop
        or _pooled_client_transport is not _resolve_transport
    ):
        client_kwargs: dict[str, Any] = {"timeout": 10.0}
        if _resolve_transport is not None:
            client_kwargs["transport"] = _resolve_transport
        _pooled_client = httpx.AsyncClient(**client_kwargs)
        _pooled_client_loop = loop
        _pooled_client_transport = _resolve_transport
    return _pooled_client


async def close_resolve_client() -> None:
    """Close the pooled resolve client (tidy teardown; optional)."""
    global _pooled_client
    if _pooled_client is not None and not _pooled_client.is_closed:
        await _pooled_client.aclose()
    _pooled_client = None


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



def split_qualified_value(raw_id: str) -> tuple[str | None, str]:
    """Split a qualified reference value into (namespace, value).

    The platform's cross-namespace value form for non-term entities:
    bare ``VALUE`` never crosses namespaces (returns ``(None, raw_id)``);
    explicit ``NS:VALUE`` names the namespace to resolve in. The split is
    purely syntactic — first colon wins, no existence check — so callers
    get deterministic resolution independent of namespace lifecycle.

    Terms use a 2/3-part form instead (``TERMINOLOGY:VALUE`` /
    ``NS:TERMINOLOGY:VALUE``) — do not use this helper for terms.
    """
    if ":" in raw_id:
        ns_prefix, value = raw_id.split(":", 1)
        return ns_prefix, value
    return None, raw_id


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
        ns_prefix, value = split_qualified_value(raw_id)
        if ns_prefix is not None:
            # NS:VALUE — cross-namespace
            return {"ns": ns_prefix, "type": entity_type, "value": value}
        # Bare value — own namespace
        return {"ns": namespace, "type": entity_type, "value": value}


def _build_resolve_payload(
    raw_id: str,
    entity_type: str,
    namespace: str,
    include_statuses: list[str] | None = None,
) -> dict[str, Any]:
    """Build a single item for the /resolve request payload.

    UUID-shaped IDs are sent as ``entry_id`` for direct verification.
    Non-term, non-UUID identifiers are sent with BOTH fields — the endpoint
    tries ``entry_id`` first, then the composite key — because canonical ids
    are NOT always UUID-shaped: a namespace's id_config can mint prefixed
    ids (e.g. ``LOV-000042``), and routing those to composite-key-only
    resolution 404s the canonical id itself. For a qualified identifier
    (``NS:VALUE``) the entry_id candidate is the value half: an entry_id
    never carries a namespace qualifier, and canonical-id verification is
    namespace-free (matching the UUID door's semantics). Terms keep their
    strict forms — composite key only for non-UUID term identifiers.
    """
    payload: dict[str, Any] = {}
    if _looks_like_uuid(raw_id):
        payload["entry_id"] = raw_id
    else:
        if entity_type != "term":
            _, value = split_qualified_value(raw_id)
            payload["entry_id"] = value
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
    """Raised when synonym resolution finds no matching entity.

    When the namespace is supplied, a bare (unqualified) identifier gets the
    reason it failed appended: bare values resolve in the caller's own
    namespace only and never fall back to allowed_external_refs, so the entity
    can exist, be legitimately referenceable, and still not be found under this
    name. Without that sentence the message describes a missing entity when the
    real problem is an under-specified identifier — a distinction that has cost
    more than one reader a day.
    """

    def __init__(self, identifier: str, entity_type: str, namespace: str | None = None):
        self.identifier = identifier
        self.entity_type = entity_type
        self.namespace = namespace
        msg = f"No {entity_type} found for identifier: {identifier}"
        if namespace:
            msg += f" (searched namespace '{namespace}')"
            if self._qualifying_would_help(identifier, entity_type):
                msg += (
                    f". A bare value resolves in '{namespace}' only and does not "
                    f"fall back to allowed_external_refs; to reference another "
                    f"namespace qualify it, e.g. 'other-ns:{identifier}'"
                )
        super().__init__(msg)

    @staticmethod
    def _qualifying_would_help(identifier: str, entity_type: str) -> bool:
        """Only hint where a qualified form is both available and absent.

        Terms are excluded: their cross-namespace form is the 3-part
        ns:terminology:value, so this 2-part advice would be wrong for them.
        Already-qualified identifiers and canonical UUIDs are excluded because
        neither is under-specified — for those the entity really is missing.
        """
        if entity_type == "term" or ":" in identifier:
            return False
        return not _UUID_PATTERN.match(identifier)


class AmbiguousEntityError(EntityNotFoundError):
    """A value resolves to several entities, and nothing narrows it to one.

    A subclass of EntityNotFoundError so every caller that refuses an
    unresolvable identifier refuses this one too — fail-closed — while the
    message names the candidates instead of claiming nothing was found.
    Raised instead of picking one: an arbitrary pick resolves to the wrong
    entity some of the time.
    """

    def __init__(self, identifier: str, entity_type: str, candidates: list[dict[str, Any]], total: int):
        Exception.__init__(self)
        self.identifier = identifier
        self.entity_type = entity_type
        self.namespace = None
        self.candidates = candidates
        self.total = total
        keys = ", ".join(
            f"{c.get('entry_id')} via {c.get('matched_composite_key') or c.get('matched_via')}"
            for c in candidates[:5]
        )
        self.args = (
            f"{entity_type} identifier '{identifier}' is ambiguous: it resolves to "
            f"{total} entities ({keys}{', …' if total > 5 else ''}); use a canonical ID "
            "or a qualified value",
        )


async def resolve_entity_id(
    raw_id: str,
    entity_type: str,
    namespace: str,
    include_statuses: list[str] | None = None,
    *,
    bypass_cache: bool = False,
) -> str:
    """Resolve any identifier to its canonical ID via Registry.

    Both synonyms and canonical IDs are verified against Registry.
    Results are cached for 5 minutes.

    Args:
        raw_id: The identifier (canonical ID or human-readable synonym)
        entity_type: Singular entity type: terminology, term, template, document
        namespace: Current namespace for resolution context
        bypass_cache: When True, skip the cache read and always query
            Registry. Intended for write paths (e.g., template creation)
            where a resolved ID will be persisted as a reference: stale
            cached IDs cannot be allowed to enter durable state. See
            CASE-56. The fresh Registry result is still cached on return,
            which self-heals any prior stale entry under the same key.

    Returns:
        The canonical entity ID

    Raises:
        EntityNotFoundError: If the identifier is not found in Registry
    """
    cache_key = f"{namespace}:{entity_type}:{raw_id}"
    if _cache_readable(bypass_cache):
        cached = _get_cached(cache_key)
        if cached:
            return cached

    payload = _build_resolve_payload(raw_id, entity_type, namespace, include_statuses)
    try:
        canonical_id = await _post_resolve(payload, raw_id, entity_type, namespace)
    except EntityNotFoundError:
        if entity_type == "term" or _looks_like_uuid(raw_id):
            raise
        # Miss-path fallback to /entries/lookup/candidates — the WRITE door's
        # transport (registry_client.resolve_identifier): its value match
        # covers value-form references whose synonym key shapes /resolve
        # cannot hash-match (a document's value synonym is the qualified
        # identity-values dict, so no {ns, type, value} key ever finds it).
        # Without this, the exact string stored in a reference field
        # validates on write and 404s on read. A value several entities carry
        # raises AmbiguousEntityError rather than resolving to one of them.
        # Terms are excluded (their strict forms resolve via composite keys
        # only), UUIDs are excluded (entry_id verification already failed —
        # the entity is missing).
        ns_prefix, value = split_qualified_value(raw_id)
        canonical_id_or_none = await _post_lookup_by_id(
            value, ns_prefix or namespace, entity_type,
        )
        if canonical_id_or_none is None:
            raise
        canonical_id = canonical_id_or_none
    _set_cached(cache_key, canonical_id)
    return canonical_id


async def _post_resolve(
    payload: dict[str, Any],
    identifier: str,
    entity_type: str,
    namespace: str | None = None,
) -> str:
    """POST one item to Registry /resolve and return the canonical id.

    Shared HTTP + error mapping for every resolution entry point — one
    implementation so transport injection and failure semantics cannot
    drift between the string-form and field-form resolvers. Raises
    EntityNotFoundError on unreachable Registry, non-200, or not-found.
    """
    registry_url = _get_registry_url()
    api_key = _get_api_key()

    try:
        client = _get_resolve_client()
        response = await client.post(
            f"{registry_url}/api/registry/entries/resolve",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json=[payload],
        )
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as e:
        logger.debug("Registry unreachable for resolution: %s", e)
        raise EntityNotFoundError(identifier, entity_type) from e

    if response.status_code != 200:
        logger.warning(
            "Registry resolve failed for %s (%s): %s",
            identifier, entity_type, response.status_code,
        )
        raise EntityNotFoundError(identifier, entity_type)

    data = response.json()
    results = data.get("results", [])
    if results and results[0].get("status") == "found":
        return cast(str, results[0]["entry_id"])

    # Genuine "Registry looked and found nothing" — the only case where an
    # under-specified identifier is a plausible cause, so the only one that
    # gets the namespace hint. The transport and non-200 raises above are
    # infrastructure failures; suggesting a qualified form there would send
    # the reader after the wrong problem.
    raise EntityNotFoundError(identifier, entity_type, namespace)


# Registry entity_type values are plural; the resolve layer speaks singular.
_ENTITY_TYPE_PLURAL = {
    "terminology": "terminologies",
    "term": "terms",
    "template": "templates",
    "document": "documents",
}


async def _post_lookup_by_id(
    value: str,
    namespace: str,
    entity_type: str,
) -> str | None:
    """The one entity a value names, via Registry /entries/lookup/candidates;
    None when there is none. Raises AmbiguousEntityError when there are
    several — never an arbitrary one of them.

    The candidates match entry_id exactly, then the values of each entry's
    active keys (where identity-values synonyms live), filtered by namespace
    and plural entity_type. A Registry predating the route (404, during a
    rollout) is asked by-id instead. Any other failure returns None: this is
    a miss-path fallback, and the caller re-raises the original
    EntityNotFoundError so the error names the identifier as given.
    """
    registry_url = _get_registry_url()
    api_key = _get_api_key()
    plural = _ENTITY_TYPE_PLURAL.get(entity_type, f"{entity_type}s")
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    try:
        client = _get_resolve_client()
        response = await client.post(
            f"{registry_url}/api/registry/entries/lookup/candidates",
            headers=headers,
            json=[{"value": value, "namespace": namespace, "entity_type": plural}],
        )
        if response.status_code == 404:
            response = await client.post(
                f"{registry_url}/api/registry/entries/lookup/by-id",
                headers=headers,
                json=[{"entry_id": value, "namespace": namespace, "entity_type": plural}],
            )
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as e:
        logger.debug("Registry unreachable for value fallback: %s", e)
        return None

    if response.status_code != 200:
        logger.warning(
            "Registry value fallback failed for %s (%s): %s",
            value, entity_type, response.status_code,
        )
        return None

    results = response.json().get("results", [])
    if not results or results[0].get("status") != "found":
        return None
    hit = results[0]
    if "candidates" not in hit:  # by-id answer from an older Registry
        return cast(str, hit.get("entry_id"))
    candidates = hit.get("candidates") or []
    total = int(hit.get("total") or len(candidates))
    if total > 1:
        raise AmbiguousEntityError(value, entity_type, candidates, total)
    return cast(str, candidates[0]["entry_id"]) if candidates else None


class TermNamespaceRequiredError(EntityNotFoundError):
    """A field-form term lookup named its terminology by a bare value, and
    there is no namespace to read that value in."""


async def terminology_coordinates_by_id(entry_id: str) -> tuple[str, str] | None:
    """The (namespace, value) of the terminology with this canonical id, or None.

    A term's Registry synonym records its terminology by VALUE, in the
    terminology's own namespace, so a terminology named by id has to be
    turned into those two coordinates before a term can be found under it.
    The id needs no namespace context: the Registry entry carries its own.
    Only an exact entry_id match counts — the lookup's search_values
    fallback would also match a bare value in some other namespace.
    """
    registry_url = _get_registry_url()
    api_key = _get_api_key()
    try:
        client = _get_resolve_client()
        response = await client.post(
            f"{registry_url}/api/registry/entries/lookup/by-id",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json=[{"entry_id": entry_id, "entity_type": "terminologies"}],
        )
    except (httpx.ConnectError, httpx.TimeoutException, OSError) as e:
        logger.debug("Registry unreachable for terminology lookup: %s", e)
        return None
    if response.status_code != 200:
        return None

    results = response.json().get("results", [])
    if not results:
        return None
    hit = results[0]
    if hit.get("status") != "found" or hit.get("matched_via") != "entry_id":
        return None
    value = (hit.get("matched_composite_key") or {}).get("value")
    if value is None:
        # Older entries keep the value only in the terminology's synonym.
        for syn in hit.get("synonyms") or []:
            key = syn.get("composite_key") or {}
            if key.get("type") == "terminology" and "value" in key:
                value = key["value"]
                break
    if value is None or not hit.get("namespace"):
        return None
    return cast(str, hit["namespace"]), cast(str, value)


async def _resolve_term_at(
    namespace: str, terminology_value: str, value: str
) -> str:
    """One field-form lookup: the term `value` under the terminology whose
    value is `terminology_value`, in `namespace`."""
    payload: dict[str, Any] = {
        "composite_key": {
            "ns": namespace,
            "type": "term",
            "terminology": terminology_value,
            "value": value,
        }
    }
    return await _post_resolve(
        payload, f"{terminology_value}/{value}", "term", namespace
    )


async def resolve_term_by_fields(
    value: str,
    terminology: str,
    namespace: str | None,
    *,
    bypass_cache: bool = False,
) -> str:
    """Resolve a term to its canonical ID from structured fields.

    The field-form door: the composite key is built directly from the
    (namespace, terminology, value) tuple, so the value is an OPAQUE
    scalar — a value containing ':' (OBO ids like 'GO:0000278') resolves
    exactly like any other. The colon-notation string parser is never
    involved. Identity lives in fields; delimiter conventions belong to
    format parsers at import boundaries, not to WIP-internal addressing.

    The terminology may be named any way a terminology can be:
    - its canonical id — needs no namespace;
    - ``NS:VALUE`` — names its own namespace;
    - a bare value — read in ``namespace``.

    A bare value is tried first, as the common case. A colon-carrying
    terminology is also tried as a literal value, so a terminology whose
    value contains ':' still resolves. A bare value that misses is looked
    up once more as a canonical id, which covers prefixed ids such as
    ``LOV-000042``.

    Args:
        value: The term's raw value, uninterpreted.
        terminology: The terminology's id, ``NS:VALUE`` or value.
        namespace: The namespace a bare terminology value is read in.
        bypass_cache: As on resolve_entity_id — write paths must not
            answer from cache.

    Raises:
        TermNamespaceRequiredError: the terminology is a bare value and
            there is no namespace to read it in.
        EntityNotFoundError: no term matches.
    """
    cache_key = f"{namespace}:term-fields:{terminology}:{value}"
    if _cache_readable(bypass_cache):
        cached = _get_cached(cache_key)
        if cached:
            return cached

    candidates: list[tuple[str, str]] = []
    if not _looks_like_uuid(terminology):
        if ":" in terminology:
            ns_prefix, terminology_value = split_qualified_value(terminology)
            candidates.append((cast(str, ns_prefix), terminology_value))
        if namespace is not None:
            candidates.append((namespace, terminology))

    canonical_id: str | None = None
    for candidate_ns, terminology_value in candidates:
        try:
            canonical_id = await _resolve_term_at(candidate_ns, terminology_value, value)
            break
        except EntityNotFoundError:
            continue

    if canonical_id is None and ":" not in terminology:
        coordinates = await terminology_coordinates_by_id(terminology)
        if coordinates is not None:
            canonical_id = await _resolve_term_at(*coordinates, value)
        elif not candidates:
            raise TermNamespaceRequiredError(f"{terminology}/{value}", "term")

    if canonical_id is None:
        raise EntityNotFoundError(f"{terminology}/{value}", "term", namespace)
    _set_cached(cache_key, canonical_id)
    return canonical_id


# Chunk size for batch resolve calls — large bulk doors (a 35k-relation
# seed) must not send one unbounded list; mirrors the term door's
# registry sub-batching.
_RESOLVE_CHUNK = 200


async def resolve_entity_ids(
    raw_ids: list[str],
    entity_type: str,
    namespace: str,
    include_statuses: list[str] | None = None,
    *,
    bypass_cache: bool = False,
    strict: bool = True,
) -> dict[str, str]:
    """Batch resolve multiple identifiers via Registry.

    Both synonyms and canonical IDs are verified. Cached results are
    used where available; uncached IDs are resolved in chunked batch
    calls (one Registry round-trip per _RESOLVE_CHUNK ids, not per id).

    Args:
        raw_ids: List of identifiers (mix of canonical and synonym)
        entity_type: Singular entity type
        namespace: Current namespace
        strict: When True (default), the first unresolvable identifier
            raises — the historical batch-door contract, relied on by
            comparison sites that treat "different or missing" alike.
            When False (the bulk WRITE doors), a per-item miss first
            tries the by-id fallback door exactly as the single-resolve
            path does — batch and single resolution stay semantically
            identical — and an identifier that still misses is simply
            ABSENT from the returned map; the caller's downstream
            BulkResponse reports it per item.

    Returns:
        Dict mapping each raw_id to its canonical ID (misses absent in
        non-strict mode).

    Raises:
        EntityNotFoundError: strict mode only — an identifier not found.
    """
    result: dict[str, str] = {}
    to_resolve: list[str] = []

    # bypass_cache: caller is doing a write that will persist the resolved
    # ID (e.g., template_service._normalize_field_references storing
    # terminology_ref as a canonical UUID on a template field). A stale
    # cache entry would bake a dead UUID into durable state — see CASE-56.
    # Skip reads; writes still update the cache with the fresh Registry
    # result below (self-heals any prior stale entry).
    seen: set[str] = set()
    for raw_id in raw_ids:
        if raw_id in seen:
            continue
        seen.add(raw_id)
        if _cache_readable(bypass_cache):
            cache_key = f"{namespace}:{entity_type}:{raw_id}"
            cached = _get_cached(cache_key)
            if cached:
                result[raw_id] = cached
                continue
        to_resolve.append(raw_id)

    if not to_resolve:
        return result

    registry_url = _get_registry_url()
    api_key = _get_api_key()

    for start in range(0, len(to_resolve), _RESOLVE_CHUNK):
        chunk = to_resolve[start:start + _RESOLVE_CHUNK]
        payloads = [
            _build_resolve_payload(rid, entity_type, namespace, include_statuses)
            for rid in chunk
        ]
        try:
            client = _get_resolve_client()
            response = await client.post(
                f"{registry_url}/api/registry/entries/resolve",
                headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                json=payloads,
            )
        except (httpx.ConnectError, httpx.TimeoutException, OSError) as e:
            logger.debug("Registry unreachable for batch resolution: %s", e)
            raise EntityNotFoundError(chunk[0], entity_type) from e

        if response.status_code != 200:
            logger.warning("Registry batch resolve failed: %s", response.status_code)
            raise EntityNotFoundError(chunk[0], entity_type)

        data = response.json()
        for i, resolve_result in enumerate(data.get("results", [])):
            raw_id = chunk[i]
            if resolve_result.get("status") == "found":
                canonical_id = resolve_result["entry_id"]
                cache_key = f"{namespace}:{entity_type}:{raw_id}"
                _set_cached(cache_key, canonical_id)
                result[raw_id] = canonical_id
                continue
            if strict:
                # Same reasoning as the single-resolve path: a per-item miss
                # is a real lookup failure, so it carries the namespace hint.
                raise EntityNotFoundError(raw_id, entity_type, namespace)
            # Non-strict miss: the single door falls back to the by-id
            # lookup for non-term, non-UUID identifiers (value-form
            # references whose synonym key shapes /resolve cannot
            # hash-match) — the batch door must not be weaker, or moving a
            # caller from per-item to batch resolution would silently break
            # exactly those references. Misses are rare; the per-miss call
            # does not reintroduce the per-item round-trip storm.
            if entity_type != "term" and not _looks_like_uuid(raw_id):
                ns_prefix, value = split_qualified_value(raw_id)
                try:
                    fallback = await _post_lookup_by_id(
                        value, ns_prefix or namespace, entity_type,
                    )
                except AmbiguousEntityError:
                    # Non-strict: an ambiguous value stays unresolved, like a
                    # miss — the batch never picks one of its candidates.
                    fallback = None
                if fallback is not None:
                    _set_cached(f"{namespace}:{entity_type}:{raw_id}", fallback)
                    result[raw_id] = fallback

    return result
