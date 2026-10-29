"""Keep the Registry's synonym state in line with entities' lifecycle.

A retired entity's synonyms are released: they stay recorded on its Registry
entry as inactive, so they neither block another entity from claiming the key
nor resolve. Its canonical ID, primary key and auto-synonym keep resolving —
retired is not gone, and re-posting the same identity still revives it. A
revived entity claims its synonyms again where nobody took them meanwhile.

Each store tells the Registry as it retires or revives (``sync_entry_lifecycle``)
and offers a maintenance pass (``reconcile_synonym_lifecycle``) for what those
calls could not cover: entities retired before the Registry learned about
retirement, and calls that failed. The store supplies only which of its IDs
are retired and which are active; the rest is the same everywhere.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass, field
from typing import Any

from .registry_client import RegistryClientBase

logger = logging.getLogger(__name__)


async def sync_entry_lifecycle(
    registry: RegistryClientBase,
    entry_id: str,
    *,
    retired: bool,
    actor: str | None,
    noun: str,
) -> list[str]:
    """Tell the Registry an entity was retired or revived; return warnings.

    The entity's own write has already committed, so a failure is reported,
    never raised: its synonyms keep their previous state until the store's
    reconcile-synonym-lifecycle pass runs. A revival that could not reclaim a
    synonym (another entity took it) comes back as the Registry's warning.
    """
    verb = "retired" if retired else "revived"
    try:
        if retired:
            results = await registry.deactivate_entries([entry_id], updated_by=actor)
        else:
            results = await registry.reactivate_entries([entry_id], updated_by=actor)
    except Exception as e:
        logger.warning("Registry not told %s %s was %s: %s", noun, entry_id, verb, e)
        return [
            f"The Registry was not told this {noun} was {verb} ({e}); its "
            "synonyms keep their previous state until "
            "/maintenance/reconcile-synonym-lifecycle runs"
        ]
    warnings: list[str] = []
    for r in results:
        warnings.extend(r.get("warnings") or [])
        if r.get("status") in ("error", "not_found"):
            warnings.append(
                f"The Registry could not record this {noun} as {verb}: "
                f"{r.get('error') or r.get('status')}"
            )
    return warnings


@dataclass
class LifecycleItem:
    """One entity the pass changed, or could not handle."""

    entry_id: str
    status: str  # released | reclaimed | not_reclaimed | not_found | error
    keys: list[dict[str, Any]] = field(default_factory=list)
    detail: str | None = None


@dataclass
class LifecycleReport:
    """The pass result."""

    namespace: str
    dry_run: bool
    revive: bool
    retired_scanned: int = 0
    released: int = 0
    active_scanned: int = 0
    reclaimed: int = 0
    not_reclaimed: int = 0
    unchanged: int = 0
    not_found: int = 0
    errors: int = 0
    items: list[LifecycleItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "dry_run": self.dry_run,
            "revive": self.revive,
            "retired_scanned": self.retired_scanned,
            "released": self.released,
            "active_scanned": self.active_scanned,
            "reclaimed": self.reclaimed,
            "not_reclaimed": self.not_reclaimed,
            "unchanged": self.unchanged,
            "not_found": self.not_found,
            "errors": self.errors,
            "items": [vars(i) for i in self.items],
        }

    def _failed(self, r: dict[str, Any], entry_id: str) -> bool:
        status = r.get("status")
        if status == "not_found":
            self.not_found += 1
            self.items.append(LifecycleItem(entry_id, "not_found"))
            return True
        if status == "error" or status is None:
            self.errors += 1
            self.items.append(LifecycleItem(entry_id, "error", detail=r.get("error")))
            return True
        return False


IdBatches = Callable[[], AsyncIterable[list[str]]]


async def reconcile_synonym_lifecycle(
    registry: RegistryClientBase,
    namespace: str,
    retired_ids: IdBatches,
    active_ids: IdBatches,
    *,
    dry_run: bool = True,
    revive: bool = False,
) -> LifecycleReport:
    """Release the synonyms of every retired entity and, with revive=True
    (applied only — it touches every active entity), reclaim those of every
    active one. Dry-run reports what retiring would release. Idempotent."""
    report = LifecycleReport(namespace=namespace, dry_run=dry_run, revive=revive)

    async for batch in retired_ids():
        report.retired_scanned += len(batch)
        results = await registry.deactivate_entries(batch, dry_run=dry_run)
        for entry_id, r in zip(batch, results, strict=False):
            if report._failed(r, entry_id):
                continue
            if r["status"] == "deactivated":
                report.released += 1
                report.items.append(LifecycleItem(entry_id, "released", r.get("released") or []))
            else:
                report.unchanged += 1

    if revive and not dry_run:
        async for batch in active_ids():
            report.active_scanned += len(batch)
            results = await registry.reactivate_entries(batch)
            for entry_id, r in zip(batch, results, strict=False):
                if report._failed(r, entry_id):
                    continue
                if r.get("reclaimed"):
                    report.reclaimed += 1
                    report.items.append(LifecycleItem(entry_id, "reclaimed", r["reclaimed"]))
                if r.get("not_reclaimed"):
                    report.not_reclaimed += 1
                    report.items.append(LifecycleItem(
                        entry_id, "not_reclaimed",
                        [n["composite_key"] for n in r["not_reclaimed"]],
                        detail="; ".join(r.get("warnings") or []),
                    ))
                if r.get("status") == "unchanged":
                    report.unchanged += 1

    logger.info(
        "Synonym lifecycle pass on %s (dry_run=%s, revive=%s): released=%d reclaimed=%d "
        "not_reclaimed=%d not_found=%d errors=%d",
        namespace, dry_run, revive, report.released, report.reclaimed,
        report.not_reclaimed, report.not_found, report.errors,
    )
    return report


async def batched(ids: AsyncIterable[str], size: int = 500) -> AsyncIterable[list[str]]:
    """Group a stream of IDs into lists of at most ``size``."""
    batch: list[str] = []
    async for entry_id in ids:
        batch.append(entry_id)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
