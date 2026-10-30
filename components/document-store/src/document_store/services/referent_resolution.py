"""Resolve an archive's out-of-archive referents on the target install.

A backup records, for every entity its rows reference but it does not carry,
that entity's Registry entry: primary composite key and synonyms (see
``wip_archive.referents``). A restore that re-mints ids (fresh) or lands in a
namespace that is not the source (merge) passes those references through
unchanged, so they keep the SOURCE install's ids. This module asks the
target's Registry, the identity authority, which of its entities each
referent is, so the restore can rewrite the references with the same id
maps it uses for the entities it carries.

Resolution is by the referent's own keys, never by its old id: a canonical id
is not portable (a prefixed id such as ``LOV-000042`` can name a different
entity on another install), its keys are. On the source install every key
resolves to the referent itself, so a same-install restore maps each id to
itself. On another install the install-specific keys (a document's primary
key embeds its template's id) find nothing and the portable ones
(``{ns, type, value}``, a document's template-qualified identity synonym)
find the target's entity.

A referent is mapped only when its keys agree on exactly one entity. None
found and several found are both reported, never guessed: the reference then
keeps its archived id and the namespace validation job flags it.

Retired referents resolve too (PoNIF 1: inactive means retired, and existing
references keep resolving), so the lookup includes inactive entries. Reserved
entries do not resolve: those are an in-flight restore's, including this one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# Items per POST to the Registry's /entries/resolve.
RESOLVE_BATCH = 500

# What a referent may resolve to: live and retired entities, never reserved.
_RESOLVABLE_STATUSES = ["active", "inactive"]

ResolveFn = Callable[[list[dict[str, Any]]], Awaitable[list[str | None]]]


@dataclass
class UnresolvedReferent:
    """A referent the target install could not identify, and why."""

    entity_type: str
    entry_id: str
    namespace: str
    key: dict[str, Any]
    reason: str  # "not_found" | "ambiguous"
    candidates: list[str] = field(default_factory=list)

    def describe(self) -> str:
        key = ", ".join(f"{k}={v}" for k, v in self.key.items())
        if self.reason == "ambiguous":
            return (
                f"{self.entity_type} {self.entry_id} ({key}) matches several "
                f"entities here: {', '.join(self.candidates)}"
            )
        return f"{self.entity_type} {self.entry_id} ({key}) is not on this install"


@dataclass
class ReferentResolution:
    """Old id -> target id per entity type, plus what did not resolve."""

    mapping: dict[str, dict[str, str]] = field(default_factory=dict)
    unresolved: list[UnresolvedReferent] = field(default_factory=list)


def referent_keys(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve items for one referent row: its primary key, then every
    synonym, each scoped to the namespace and entity type it was registered
    under."""
    entity_type = row.get("entity_type")
    items: list[dict[str, Any]] = []
    primary = row.get("primary_composite_key")
    if primary:
        items.append({
            "composite_key": primary,
            "namespace": row.get("namespace"),
            "entity_type": entity_type,
            "include_statuses": _RESOLVABLE_STATUSES,
        })
    for synonym in row.get("synonyms") or []:
        key = synonym.get("composite_key")
        if key:
            items.append({
                "composite_key": key,
                "namespace": synonym.get("namespace") or row.get("namespace"),
                "entity_type": synonym.get("entity_type") or entity_type,
                "include_statuses": _RESOLVABLE_STATUSES,
            })
    return items


def _display_key(row: dict[str, Any]) -> dict[str, Any]:
    """The most readable key for a report: a synonym when one exists (the
    portable, value-bearing form), else the primary key."""
    for synonym in row.get("synonyms") or []:
        if synonym.get("composite_key"):
            return dict(synonym["composite_key"])
    return dict(row.get("primary_composite_key") or {})


async def resolve_referents(
    rows: list[dict[str, Any]], resolve: ResolveFn
) -> ReferentResolution:
    """Resolve every referent row through its keys; see the module docstring.

    ``resolve`` takes /entries/resolve items and returns, per item, the
    entry_id found or None. It is called in batches of RESOLVE_BATCH.
    """
    items: list[dict[str, Any]] = []
    owner: list[int] = []  # item index -> row index
    for index, row in enumerate(rows):
        for item in referent_keys(row):
            items.append(item)
            owner.append(index)

    found: list[set[str]] = [set() for _ in rows]
    for start in range(0, len(items), RESOLVE_BATCH):
        answers = await resolve(items[start : start + RESOLVE_BATCH])
        for offset, entry_id in enumerate(answers):
            if entry_id:
                found[owner[start + offset]].add(entry_id)

    result = ReferentResolution()
    for row, hits in zip(rows, found, strict=True):
        entity_type = row.get("entity_type") or "?"
        old_id = row.get("entry_id") or "?"
        if len(hits) == 1:
            (new_id,) = hits
            result.mapping.setdefault(entity_type, {})[old_id] = new_id
            continue
        result.unresolved.append(UnresolvedReferent(
            entity_type=entity_type,
            entry_id=old_id,
            namespace=row.get("namespace") or "",
            key=_display_key(row),
            reason="ambiguous" if hits else "not_found",
            candidates=sorted(hits),
        ))
    return result
