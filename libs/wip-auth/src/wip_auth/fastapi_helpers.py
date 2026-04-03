"""FastAPI helper functions for synonym resolution at API boundaries.

Replaces the `contextlib.suppress(EntityNotFoundError)` anti-pattern with
explicit resolution that raises HTTP 404 on failure.

Usage:
    from wip_auth.fastapi_helpers import resolve_or_404, resolve_bulk_ids

    # Single ID resolution (raises 404 on failure)
    template_id = await resolve_or_404(template_id, "template", namespace)

    # Bulk resolution (mutates items in place)
    await resolve_bulk_ids(items, "template_id", "template", namespace)
"""

import logging

from fastapi import HTTPException

from .resolve import EntityNotFoundError, is_canonical_format, resolve_entity_id

logger = logging.getLogger(__name__)


async def resolve_or_404(
    raw_id: str,
    entity_type: str,
    namespace: str | None,
    *,
    param_name: str | None = None,
) -> str:
    """Resolve a synonym to a canonical ID, raising HTTP 404 on failure.

    If ``raw_id`` is already canonical (UUID), returns it unchanged.
    If ``namespace`` is None, skips resolution and returns raw_id as-is
    (caller must handle value-based fallback).

    Args:
        raw_id: The identifier to resolve (UUID or human-readable synonym).
        entity_type: Entity type for resolution (terminology, term, template, document).
        namespace: Namespace for resolution context. If None, resolution is skipped.
        param_name: Optional parameter name for error messages.

    Returns:
        Canonical entity ID (UUID).

    Raises:
        HTTPException(404): When the synonym cannot be resolved.
    """
    if is_canonical_format(raw_id):
        return raw_id

    if namespace is None:
        # Without namespace, cannot resolve — return raw for value-based fallback
        return raw_id

    try:
        return await resolve_entity_id(raw_id, entity_type, namespace)
    except EntityNotFoundError:
        label = param_name or entity_type
        raise HTTPException(
            status_code=404,
            detail=f"Could not resolve {label} '{raw_id}' in namespace '{namespace}'",
        ) from None


async def resolve_bulk_ids(
    items: list,
    id_field: str,
    entity_type: str,
    namespace: str | None,
) -> None:
    """Batch-resolve IDs on bulk request items, mutating in place.

    For each item, resolves ``getattr(item, id_field)`` from synonym to
    canonical ID. Items with canonical IDs are left untouched.

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
    for item in items:
        # Get the raw ID
        if isinstance(item, dict):
            raw_id = item.get(id_field)
            item_ns = namespace or item.get("namespace")
        else:
            raw_id = getattr(item, id_field, None)
            item_ns = namespace or getattr(item, "namespace", None)

        if not raw_id or is_canonical_format(raw_id):
            continue

        if not item_ns:
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
