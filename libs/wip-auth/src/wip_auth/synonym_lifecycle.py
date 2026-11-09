"""Keep the Registry's synonym state in line with entities' lifecycle.

A retired entity's synonyms are released: they stay recorded on its Registry
entry as inactive, so they neither block another entity from claiming the key
nor resolve. Its canonical ID, primary key and auto-synonym keep resolving —
retired is not gone, and re-posting the same identity still revives it. A
revived entity claims its synonyms again where nobody took them meanwhile.

Each store has the Registry record a retirement or revival before it writes
one (``entry_lifecycle``): when the Registry cannot record it, the store
writes nothing. A maintenance pass (``reconcile_synonym_lifecycle``) covers
what those calls could not: entities retired before the Registry learned
about retirement, and an undo that failed. The store supplies only which of
its IDs are retired and which are active; the rest is the same everywhere.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from .registry_client import RegistryClientBase, RegistryError, RegistryUnavailableError

logger = logging.getLogger(__name__)


class LifecycleRefused(RegistryError):
    """The Registry answered, but did not record every entry as retired or
    revived (an entry it refused or does not know, or an incomplete answer).

    A ``RegistryError``: stores report it as ``registry_error``. A Registry
    that does not answer at all raises ``RegistryUnavailableError`` instead
    (HTTP 503), never this.
    """


def _refusal(results: list[dict[str, Any]], expected: int) -> tuple[list[str], str] | None:
    """None when the Registry recorded every entry; else the entries it did
    record (to undo) and what it refused."""
    refused = [r for r in results if r.get("status") in ("error", "not_found")]
    if not refused and len(results) == expected:
        return None
    recorded = [r["registry_id"] for r in results if r not in refused and r.get("registry_id")]
    return recorded, _refusal_detail(refused)


def _refusal_detail(refused: list[dict[str, Any]]) -> str:
    """What the Registry refused, for the error message (at most five)."""
    detail = "; ".join(
        f"{r.get('registry_id')}: {r.get('status')} {r.get('error') or ''}".strip()
        for r in refused[:5]
    )
    return detail or "the Registry did not answer every entry"


async def _change(
    registry: RegistryClientBase, entry_ids: list[str], *, retired: bool, actor: str | None,
) -> list[dict[str, Any]]:
    if retired:
        return await registry.deactivate_entries(entry_ids, updated_by=actor)
    return await registry.reactivate_entries(entry_ids, updated_by=actor)


async def record_entry_lifecycle(
    registry: RegistryClientBase,
    entry_ids: list[str],
    *,
    retired: bool,
    actor: str | None,
) -> list[str]:
    """Have the Registry record these entities as retired or revived BEFORE
    the store writes the change; return the Registry's warnings.

    The Registry is the identity authority: a retirement it has not recorded
    leaves the entity's synonyms resolving as though it were alive, and a
    revival it has not recorded leaves them released. So when the Registry
    does not answer (``RegistryUnavailableError``, or whatever the call
    raised) or refuses any entry (``LifecycleRefused``), what it did record
    is undone and the error is raised: the caller writes nothing.

    The warnings are not refusals: a revived entity whose synonym another
    entity took meanwhile is revived without it, and the warning names it.
    """
    if not entry_ids:
        return []
    verb = "retire" if retired else "revive"
    try:
        results = await _change(registry, entry_ids, retired=retired, actor=actor)
    except Exception:
        # It may have recorded part of a batched request before failing.
        await undo_entry_lifecycle(registry, entry_ids, retired=retired, actor=actor)
        raise
    refusal = _refusal(results, len(entry_ids))
    if refusal is not None:
        recorded, detail = refusal
        await undo_entry_lifecycle(registry, recorded, retired=retired, actor=actor)
        raise LifecycleRefused(f"the Registry could not {verb} it ({detail})")
    warnings: list[str] = []
    for r in results:
        for warning in r.get("warnings") or []:
            logger.warning("Entry %s: %s", r.get("registry_id"), warning)
            warnings.append(warning)
    return warnings


async def undo_entry_lifecycle(
    registry: RegistryClientBase,
    entry_ids: list[str],
    *,
    retired: bool,
    actor: str | None,
) -> None:
    """Best effort: reverse a lifecycle change the Registry recorded for a
    write that is not going ahead. A failure here is logged — the write it
    served is refused either way, and the store's
    /maintenance/reconcile-synonym-lifecycle settles what is left."""
    if not entry_ids:
        return
    try:
        await _change(registry, entry_ids, retired=not retired, actor=actor)
    except Exception as e:
        logger.error("Registry lifecycle change for %d entries not undone: %s", len(entry_ids), e)


@asynccontextmanager
async def entry_lifecycle(
    registry: RegistryClientBase,
    entry_ids: list[str],
    *,
    retired: bool,
    actor: str | None,
) -> AsyncIterator[list[str]]:
    """Record the lifecycle change in the Registry, then run the block (the
    store's write); yields the Registry's warnings.

    Nothing runs when the Registry cannot record the change (see
    ``record_entry_lifecycle``); when the block raises, the Registry change
    is undone and the error propagates. No entry ids, no Registry call: the
    write does not change whether anything is retired.
    """
    warnings = await record_entry_lifecycle(registry, entry_ids, retired=retired, actor=actor)
    try:
        yield warnings
    except BaseException:
        await undo_entry_lifecycle(registry, entry_ids, retired=retired, actor=actor)
        raise


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
    aborted: str | None = None
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
            "aborted": self.aborted,
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


class SynonymLifecycleAborted(RegistryUnavailableError):
    """The Registry stopped answering part-way through the pass.

    An outage, so HTTP 503 like any other; ``report`` holds what the pass
    had already applied (its ``aborted`` says why it stopped), which the 503
    carries — those changes are real, and the pass is idempotent, so running
    it again once the Registry answers finishes the rest.
    """

    def __init__(self, report: LifecycleReport, cause: RegistryUnavailableError):
        report.aborted = str(cause)
        self.report = report
        super().__init__(
            f"Synonym lifecycle pass on {report.namespace} stopped: {cause}", cause.operation,
        )


IdBatches = Callable[[], AsyncIterable[list[str]]]


async def _answered(
    report: LifecycleReport, call: Awaitable[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """The Registry's answer to one batch of the pass; an outage stops the
    pass with what it has applied so far (SynonymLifecycleAborted)."""
    try:
        return await call
    except RegistryUnavailableError as e:
        raise SynonymLifecycleAborted(report, e) from e


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
    active one. Dry-run reports what retiring would release. Idempotent.

    A Registry that stops answering part-way raises SynonymLifecycleAborted
    (a 503) carrying the report of what was already applied; its batches
    are counted as scanned only once the Registry answered them."""
    report = LifecycleReport(namespace=namespace, dry_run=dry_run, revive=revive)

    async for batch in retired_ids():
        results = await _answered(report, registry.deactivate_entries(batch, dry_run=dry_run))
        report.retired_scanned += len(batch)
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
            results = await _answered(report, registry.reactivate_entries(batch))
            report.active_scanned += len(batch)
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
