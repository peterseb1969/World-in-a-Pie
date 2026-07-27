"""FastAPI helper functions for synonym resolution at API boundaries.

Every ID — canonical or synonym — is verified against Registry.
There is no format-based bypass.

Usage:
    from wip_auth.fastapi_helpers import resolve_or_404, resolve_bulk_ids

    # Single ID resolution (raises 404 on failure)
    template_id = await resolve_or_404(template_id, "template", namespace)

    # Bulk resolution (mutates items in place)
    await resolve_bulk_ids(items, "template_id", "template", namespace)
"""

import logging
from typing import cast

from fastapi import HTTPException

from .identity import get_current_identity
from .resolve import (
    EntityNotFoundError,
    _looks_like_uuid,
    resolve_entity_id,
    resolve_term_by_fields,
)

logger = logging.getLogger(__name__)


def _reject_ambiguous_term_form(raw_id: str, param_name: str | None) -> None:
    """Reject the lossy 2-part ``TERMINOLOGY:VALUE`` term shorthand with 422.

    A term's identity is the tuple (namespace, terminology, value). The
    2-part colon shorthand is a lossy serialization of it: no parser can
    tell a structural colon from a colon inside the value (OBO ids like
    ``GO:0000278``), so a 2-part string can silently resolve to a term in
    a decoy terminology — on a write door, that mutates the wrong entity.
    Term doors therefore accept only the unambiguous forms: canonical
    UUID, fully qualified ``ns:terminology:value`` (split on the first two
    colons, so the value keeps any colons it contains), or structured
    fields (a ``terminology`` scope with the raw value passed opaque).

    Called for every term identifier that resolves WITHOUT a terminology
    scope. Identifiers under a terminology scope are opaque values and are
    never colon-parsed, so no rejection applies there.
    """
    if _looks_like_uuid(raw_id):
        return
    if ":" not in raw_id:
        return
    if len(raw_id.split(":", 2)) == 2:
        label = param_name or "term"
        raise HTTPException(
            status_code=422,
            detail=(
                f"Ambiguous term identifier '{raw_id}' for {label}: the "
                "2-part 'TERMINOLOGY:VALUE' shorthand is not accepted — a "
                "value that itself contains ':' (e.g. OBO ids like "
                "GO:0000278) cannot be distinguished from it. Use the "
                "canonical UUID, the fully qualified "
                "'ns:terminology:value' form, or pass terminology= "
                "separately with the raw value."
            ),
        )


def _derive_namespace_from_identity() -> str | None:
    """Derive namespace from the current identity's scope.

    If the authenticated API key is scoped to exactly one namespace,
    return it. Otherwise return None (caller must provide namespace
    explicitly).
    """
    identity = get_current_identity()
    if identity is None:
        return None
    namespaces = (identity.raw_claims or {}).get("namespaces")
    if isinstance(namespaces, list) and len(namespaces) == 1:
        return cast(str | None, namespaces[0])
    return None


async def resolve_or_404(
    raw_id: str,
    entity_type: str,
    namespace: str | None,
    *,
    param_name: str | None = None,
    strict: bool = False,
) -> str:
    """Resolve any identifier to a canonical ID, raising HTTP 404 on failure.

    Both canonical IDs and synonyms are verified against Registry.
    If ``namespace`` is None, attempts to derive it from the caller's
    identity (single-namespace API keys). If derivation fails, returns
    raw_id as-is (caller must handle value-based fallback).

    Args:
        raw_id: The identifier to resolve (canonical ID or human-readable synonym).
        entity_type: Entity type for resolution (terminology, term, template, document).
        namespace: Namespace for resolution context. If None, derived from identity.
        param_name: Optional parameter name for error messages.
        strict: When True, a non-UUID identifier that cannot be resolved for
            lack of namespace context raises HTTP 422 instead of passing the
            raw value through. Use this in **filter contexts** that have no
            value-based fallback (e.g. a query's ``template_id``): there, the
            raw value flows into the storage filter, matches nothing, and the
            request "succeeds" with zero rows — a silent misconfiguration of
            the CASE-316/317/318 class (CASE-457). Endpoints that DO have a
            value fallback (a GET's by-value branch) must leave this False so
            the pass-through remains load-bearing.

    Returns:
        Canonical entity ID.

    Raises:
        HTTPException(404): When the identifier cannot be resolved.
        HTTPException(422): When ``strict`` and the identifier is a non-UUID
            value with no namespace context to resolve it against; or when
            the identifier is a term in the ambiguous 2-part colon form
            (see ``_reject_ambiguous_term_form`` — term doors accept only
            UUID, ``ns:terminology:value``, or field form).
    """
    if entity_type == "term":
        _reject_ambiguous_term_form(raw_id, param_name)

    if namespace is None:
        namespace = _derive_namespace_from_identity()

    if namespace is None and ":" in raw_id and not _looks_like_uuid(raw_id):
        # A qualified identifier carries its own namespace context — the
        # prefix names the namespace to resolve in (for terms only the
        # 3-part ns:terminology:value form reaches here; the 2-part form was
        # rejected above). "No namespace context" is false for these, so
        # resolve instead of passing the raw string through to a lookup that
        # cannot know the qualified form. A MISS returns the raw id — the
        # same outcome the pass-through produced — so this only ADDS
        # resolution; the explicit-namespace path below keeps 404-on-miss.
        from .resolve import split_qualified_value

        ns_prefix, _ = split_qualified_value(raw_id)
        try:
            return await resolve_entity_id(raw_id, entity_type, cast(str, ns_prefix))
        except EntityNotFoundError:
            if strict:
                # Filter context with real (identifier-carried) namespace
                # context and a genuine miss: same contract as the
                # explicit-namespace path — fail loud, never let the
                # unresolved value silently match nothing.
                label = param_name or entity_type
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Could not resolve {label} '{raw_id}' in namespace "
                        f"'{ns_prefix}'"
                    ),
                ) from None
            return raw_id

    if namespace is None:
        # Still no namespace — cannot resolve.
        if not _looks_like_uuid(raw_id):
            if strict:
                # No value-based fallback downstream: fail loud instead of
                # letting the unresolved value silently match nothing.
                label = param_name or entity_type
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Cannot resolve {label} '{raw_id}': no namespace "
                        "context. Pass ?namespace= or use a single-namespace "
                        "API key."
                    ),
                )
            logger.warning(
                "resolve_or_404: no namespace for %s=%s — synonym resolution skipped. "
                "Use a namespace-scoped API key or pass namespace explicitly.",
                param_name or entity_type, raw_id,
            )
        # UUID (canonical, self-resolving) or non-strict caller → pass through.
        return raw_id

    try:
        return await resolve_entity_id(raw_id, entity_type, namespace)
    except EntityNotFoundError:
        label = param_name or entity_type
        detail = f"Could not resolve {label} '{raw_id}' in namespace '{namespace}'"
        raise HTTPException(status_code=404, detail=detail) from None


async def resolve_term_by_fields_or_404(
    value: str,
    terminology: str,
    namespace: str | None,
    *,
    param_name: str = "term",
) -> str:
    """Resolve a term from structured (terminology, value) fields, 404 on miss.

    The field-form counterpart to resolve_or_404: the value is an opaque
    scalar (never colon-parsed), so colon-carrying vocabularies resolve
    like any other. If ``namespace`` is None it is derived from a
    single-namespace key; field-form resolution has no pass-through
    fallback, so a missing namespace is a 422, not a silent miss.
    """
    if namespace is None:
        namespace = _derive_namespace_from_identity()
    if namespace is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Cannot resolve {param_name} by terminology+value: no "
                "namespace context. Pass ?namespace= or use a "
                "single-namespace API key."
            ),
        )
    try:
        return await resolve_term_by_fields(value, terminology, namespace)
    except EntityNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Could not resolve {param_name} value '{value}' in "
                f"terminology '{terminology}' (namespace '{namespace}')"
            ),
        ) from None


async def resolve_bulk_ids(
    items: list,
    id_field: str,
    entity_type: str,
    namespace: str | None,
    *,
    bypass_cache: bool = False,
    terminology: str | None = None,
    terminology_field: str | None = None,
) -> None:
    """Batch-resolve IDs on bulk request items, mutating in place.

    For each item, resolves ``getattr(item, id_field)`` from any identifier
    to its canonical ID. All IDs are verified against Registry.

    Resolution failures are logged but do not raise — the downstream
    service will handle the unresolved ID (typically returning per-item errors
    in the BulkResponse). The exception is the ambiguous 2-part term
    shorthand, which is a request-shape error and raises 422 for the whole
    request (like a body-validation failure), before any item resolves.

    Args:
        items: List of request items (Pydantic models or dicts).
        id_field: Attribute name containing the ID to resolve.
        entity_type: Entity type for resolution.
        namespace: Namespace for resolution. Per-item namespace is used if
            the item has a ``namespace`` attribute and ``namespace`` is None.
        bypass_cache: Skip the resolution-cache read and always ask
            Registry. REQUIRED on write doors: a resolved ID that will be
            persisted (a deprecation's replacement pointer, a relation's
            endpoints) must not come from a stale cache entry — answering
            writes from the cache pins dead IDs into durable state. Reads
            keep the default (cache on).
        terminology: Call-scoped terminology for term resolution. When set,
            every non-UUID identifier is treated as the OPAQUE raw term
            value (never colon-parsed) and resolves through the structured
            field door — required for values that themselves contain ':'.
        terminology_field: Name of a per-item attribute carrying the
            terminology scope (for request shapes where each item scopes
            itself, e.g. a relation's ``source_terminology``). An item
            without the attribute set falls back to the string path.
    """
    # Derive namespace from identity if not provided
    if namespace is None:
        namespace = _derive_namespace_from_identity()

    def _item_context(item: object) -> tuple[str | None, str | None, str | None]:
        if isinstance(item, dict):
            raw_id = item.get(id_field)
            item_ns = namespace or item.get("namespace")
            item_terminology = terminology or (
                item.get(terminology_field) if terminology_field else None
            )
        else:
            raw_id = getattr(item, id_field, None)
            item_ns = namespace or getattr(item, "namespace", None)
            item_terminology = terminology or (
                getattr(item, terminology_field, None) if terminology_field else None
            )
        return raw_id, item_ns, item_terminology

    # Form-validation pre-pass: a request-shape error must reject the
    # request before ANY item resolves — otherwise earlier items reach
    # Registry (and populate the cache) for a request that then 422s.
    # Ambiguity is a property of the identifier's form, not the namespace,
    # so this fires even for items whose resolution would be skipped.
    if entity_type == "term":
        for item in items:
            raw_id, _, item_terminology = _item_context(item)
            if raw_id and not item_terminology:
                _reject_ambiguous_term_form(raw_id, id_field)

    for item in items:
        raw_id, item_ns, item_terminology = _item_context(item)

        if not raw_id:
            continue

        if not item_ns:
            logger.warning(
                "resolve_bulk_ids: no namespace for item %s=%s — synonym resolution skipped. "
                "Use a namespace-scoped API key or pass namespace explicitly.",
                id_field, raw_id,
            )
            continue

        try:
            if item_terminology and not _looks_like_uuid(raw_id):
                # Field door: the identifier is the raw value, uninterpreted.
                resolved = await resolve_term_by_fields(
                    raw_id, item_terminology, item_ns, bypass_cache=bypass_cache,
                )
            else:
                resolved = await resolve_entity_id(
                    raw_id, entity_type, item_ns, bypass_cache=bypass_cache,
                )
            if isinstance(item, dict):
                item[id_field] = resolved
            else:
                setattr(item, id_field, resolved)
        except EntityNotFoundError:
            logger.warning(
                "Could not resolve %s '%s' in namespace '%s' — passing through",
                entity_type, raw_id, item_ns,
            )
