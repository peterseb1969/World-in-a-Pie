"""Install-wide uniqueness of `prefixed` ID prefixes.

A `prefixed` ID is the prefix followed by a zero-padded sequence number, and
every entry_id must be unique across the whole Registry. The sequence counter,
though, is per (namespace, entity type, prefix), so two users of one prefix
mint the same IDs and the second one's writes fail on the entry_id index. A
prefix must therefore name exactly one ID sequence on the install.

The rule that guarantees it, for every `prefixed` entry of a namespace's
`id_config`:

- the prefix is non-empty, and does not end in a digit;
- no other (namespace, entity type) uses the same prefix.

The digit rule is what makes exact uniqueness enough. IDs are prefix + digits,
so `P1 + n1 == P2 + n2` with `P1 != P2` would need one prefix to be the other
plus digits — which then ends in a digit. Without it, `DOC-` and `DOC-00` would
both mint `DOC-000010`.

Enforcement is a derived field, `Namespace.id_prefixes`, with a unique multikey
index: two namespaces cannot both hold a prefix, even under concurrent writes.
A multikey index does not compare values within one document, so two entity
types of ONE namespace sharing a prefix are refused here, before the write.

Configs stored before this rule are grandfathered: a write that leaves a
namespace's `id_config` unchanged is never refused, and the startup check only
reports what it cannot claim.
"""

from __future__ import annotations

import logging
from typing import Any

from pymongo.errors import DuplicateKeyError

from ..models.namespace import ID_PREFIXES_INDEX, Namespace

logger = logging.getLogger(__name__)


class IdPrefixError(Exception):
    """A namespace write whose `prefixed` id_config breaks the rule."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def prefixed_entries(id_config: dict[str, Any] | None) -> dict[str, str]:
    """{entity_type: prefix} for every `prefixed` entry (prefix "" if missing).

    Entries that are not objects are left to the id_config shape check; they
    claim no prefix here.
    """
    out: dict[str, str] = {}
    for entity_type, cfg in (id_config or {}).items():
        if isinstance(cfg, dict) and cfg.get("algorithm") == "prefixed":
            out[entity_type] = cfg.get("prefix") or ""
    return out


def claimed_prefixes(id_config: dict[str, Any] | None) -> list[str]:
    """The distinct non-empty prefixes a config uses, sorted — the stored field."""
    return sorted({p for p in prefixed_entries(id_config).values() if p})


def check_prefix_rule(id_config: dict[str, Any] | None) -> list[str]:
    """Validate one config on its own; return the prefixes it claims.

    Raises IdPrefixError (422) for an empty prefix, a prefix ending in a
    digit, or two entity types of this config sharing a prefix.
    """
    entries = prefixed_entries(id_config)
    by_prefix: dict[str, str] = {}
    for entity_type, prefix in sorted(entries.items()):
        if not prefix:
            raise IdPrefixError(
                422,
                f"id_config.{entity_type}: the 'prefixed' algorithm needs a non-empty "
                f"prefix — without one, every prefixed sequence on the install mints "
                f"the same bare numbers",
            )
        if prefix[-1].isdigit():
            raise IdPrefixError(
                422,
                f"id_config.{entity_type}: prefix '{prefix}' ends in a digit. IDs are "
                f"prefix + digits, so it could mint the same ID as a shorter prefix "
                f"(e.g. 'DOC-00' and 'DOC-' both mint 'DOC-000010'); end it with a "
                f"non-digit",
            )
        if prefix in by_prefix:
            raise IdPrefixError(
                409,
                f"id_config: prefix '{prefix}' is used by both {by_prefix[prefix]} and "
                f"{entity_type}. Each prefix names one ID sequence on the install",
            )
        by_prefix[prefix] = entity_type
    return sorted(by_prefix)


async def assert_prefixes_free(namespace: str, prefixes: list[str]) -> None:
    """Refuse (409) a prefix another namespace already holds.

    The unique index is the real guarantee; this check runs first so the
    refusal names the holder instead of surfacing a duplicate-key error.
    """
    if not prefixes:
        return
    holder = await Namespace.find_one(
        {"id_prefixes": {"$in": prefixes}, "prefix": {"$ne": namespace}}
    )
    if holder is None:
        return
    held = prefixed_entries(holder.id_config)
    clash = sorted(set(prefixes) & set(holder.id_prefixes or []))
    detail = ", ".join(
        f"'{p}' (held by namespace '{holder.prefix}', "
        f"{'/'.join(sorted(t for t, hp in held.items() if hp == p)) or 'entity type unknown'})"
        for p in clash
    )
    raise IdPrefixError(
        409,
        f"id_config prefix already in use on this install: {detail}. A prefix names "
        f"one ID sequence install-wide — pick another",
    )


async def claim_for_write(
    namespace: str,
    new_config: dict[str, Any] | None,
    old_config: dict[str, Any] | None = None,
) -> list[str] | None:
    """The `id_prefixes` value to store with a namespace write.

    Returns None when the write leaves `id_config` unchanged (grandfathered:
    the stored claim, whatever it is, stays). Otherwise validates the new
    config and checks its prefixes are free, raising IdPrefixError.
    """
    if old_config is not None and (new_config or {}) == (old_config or {}):
        return None
    prefixes = check_prefix_rule(new_config)
    await assert_prefixes_free(namespace, prefixes)
    return prefixes


def is_prefix_collision(exc: DuplicateKeyError) -> bool:
    """True when a duplicate-key error comes from the id_prefixes index."""
    return ID_PREFIXES_INDEX in str(exc)


def collision_error(namespace: str) -> IdPrefixError:
    """The 409 for a prefix claimed concurrently by another namespace."""
    return IdPrefixError(
        409,
        f"id_config prefix for namespace '{namespace}' was claimed by another "
        f"namespace at the same time; pick another prefix",
    )


async def backfill_id_prefixes() -> list[str]:
    """Store each namespace's claimed prefixes; report what cannot be claimed.

    Runs at startup. A namespace whose stored config breaks the rule (empty
    prefix, trailing digit, a prefix shared with another entity type or
    namespace) is reported, never rewritten or refused — its config predates
    the rule. Returns the warnings, which are also logged.
    """
    warnings: list[str] = []
    async for ns in Namespace.find_all():
        entries = prefixed_entries(ns.id_config)
        if not entries:
            continue
        try:
            check_prefix_rule(ns.id_config)
        except IdPrefixError as e:
            warnings.append(f"namespace '{ns.prefix}': {e.detail}")
        wanted = claimed_prefixes(ns.id_config)
        if (ns.id_prefixes or []) == wanted:
            continue
        try:
            await Namespace.get_motor_collection().update_one(
                {"_id": ns.id}, {"$set": {"id_prefixes": wanted}}
            )
        except DuplicateKeyError:
            holders = [
                other.prefix
                async for other in Namespace.find(
                    {"id_prefixes": {"$in": wanted}, "prefix": {"$ne": ns.prefix}}
                )
            ]
            warnings.append(
                f"namespace '{ns.prefix}': prefix(es) {wanted} are already held by "
                f"{holders}; their IDs can collide. Give one of them another prefix"
            )
    for w in warnings:
        logger.warning("id_config prefix conflict: %s", w)
    return warnings
