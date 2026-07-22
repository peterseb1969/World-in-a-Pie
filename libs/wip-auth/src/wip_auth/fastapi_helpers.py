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
            value with no namespace context to resolve it against.
    """
    if namespace is None:
        namespace = _derive_namespace_from_identity()

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
        if entity_type == "term" and ":" in raw_id and len(raw_id.split(":", 2)) < 3:
            # The bare and 2-part colon forms mis-parse any VALUE that itself
            # contains ':' (OBO ids: GO:, CHEBI:, ...) — the parser cannot
            # tell a structural colon from a data colon. Point at the two
            # lossless doors instead of failing bare.
            detail += (
                ". If the term VALUE itself contains ':', the shorthand "
                "cannot parse it — use the fully qualified form "
                f"'{namespace}:<terminology>:{raw_id}', or pass "
                "terminology= separately with the raw value."
            )
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
) -> None:
    """Batch-resolve IDs on bulk request items, mutating in place.

    For each item, resolves ``getattr(item, id_field)`` from any identifier
    to its canonical ID. All IDs are verified against Registry.

    Resolution failures are logged but do not raise — the downstream
    service will handle the unresolved ID (typically returning per-item errors
    in the BulkResponse).

    Args:
        items: List of request items (Pydantic models or dicts).
        id_field: Attribute name containing the ID to resolve.
        entity_type: Entity type for resolution.
        namespace: Namespace for resolution. Per-item namespace is used if
            the item has a ``namespace`` attribute and ``namespace`` is None.
    """
    # Derive namespace from identity if not provided
    if namespace is None:
        namespace = _derive_namespace_from_identity()

    for item in items:
        # Get the raw ID
        if isinstance(item, dict):
            raw_id = item.get(id_field)
            item_ns = namespace or item.get("namespace")
        else:
            raw_id = getattr(item, id_field, None)
            item_ns = namespace or getattr(item, "namespace", None)

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
            resolved = await resolve_entity_id(raw_id, entity_type, item_ns)
            if isinstance(item, dict):
                item[id_field] = resolved
            else:
                setattr(item, id_field, resolved)
        except EntityNotFoundError:
            logger.warning(
                "Could not resolve %s '%s' in namespace '%s' — passing through",
                entity_type, raw_id, item_ns,
            )
