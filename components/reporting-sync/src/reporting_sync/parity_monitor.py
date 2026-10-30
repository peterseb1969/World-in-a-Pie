"""Standing parity check with bounded self-heal.

The restore verifies the reporting layer once, inside a time limit. A gap
that outlives that window, or appears later (a missed event, a reporting
rebuild that lost registered views), stayed invisible until a query returned
wrong results. This loop runs the namespace parity check on an interval and
repairs what it finds with the narrowest sync that closes the gap.

Rules that keep it from fighting the rest of the service:
- A gap is repaired only when two consecutive passes see it. Event-driven
  sync and the closure's rebuild debounce make short-lived gaps normal; one
  sighting is not evidence.
- A namespace with an active batch job is skipped for that pass: the
  operator's (or the restore's) sync owns it until it finishes.
- Each gap gets at most ``settings.parity_repair_attempts`` repairs. One
  that persists past them is logged once at error level and left alone
  until it closes; it stays visible in the status.
- Some gaps have no safe automatic repair (a legacy pre-split table,
  old-shape bookkeeping, a check that could not run). They are reported
  once as needing an operator and never repaired.

The last pass's result per namespace and the recent repairs are served by
``GET /parity/status``.
"""

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx
from pydantic import BaseModel, Field

from .batch_sync import BatchSyncService
from .config import settings
from .models import BatchSyncStatus
from .parity import NamespaceParityResult, check_namespace_parity
from .schema_manager import SchemaManager

logger = logging.getLogger(__name__)

# Repair order within one pass: terms before relations (the relation sync
# ends with the closure rebuild, which needs every term), and a closure
# repair only when neither of those ran.
_ORDER = ("terms", "term_relations", "term_closure", "views")


class ParityAction(BaseModel):
    at: datetime
    namespace: str
    gap: str
    action: str
    attempt: int
    error: str | None = None


class NamespaceParityStatus(BaseModel):
    checked_at: datetime | None = None
    ok: bool | None = None
    # Gaps seen this pass, by key: template:<value>, terms, term_relations,
    # term_closure, views, and the operator-only kinds.
    gaps: list[str] = Field(default_factory=list)
    # Repairs made so far per gap still open.
    repair_attempts: dict[str, int] = Field(default_factory=dict)
    # Gaps that outlived their repairs, or have no automatic repair.
    not_healing: list[str] = Field(default_factory=list)
    skipped: str | None = None
    error: str | None = None


class ParityMonitorStatus(BaseModel):
    enabled: bool
    interval_seconds: int
    repair_attempts: int
    last_pass_started_at: datetime | None = None
    last_pass_finished_at: datetime | None = None
    last_pass_error: str | None = None
    namespaces: dict[str, NamespaceParityStatus] = Field(default_factory=dict)
    recent_actions: list[ParityAction] = Field(default_factory=list)


def _gaps(result: NamespaceParityResult) -> tuple[set[str], set[str]]:
    """(repairable, operator-only) gap keys of one parity result."""
    repairable: set[str] = set()
    operator: set[str] = set()
    if not result.bookkeeping_tables_ok:
        operator.add("bookkeeping")
    for t in result.templates:
        if t.legacy_table:
            operator.add(f"legacy_table:{t.template_value}")
        elif t.error:
            operator.add(f"template_check_failed:{t.template_value}")
        elif not t.structural_ok or t.counts_match is False:
            repairable.add(f"template:{t.template_value}")
    for d in result.derived_tables:
        if d.issue == "check_failed":
            operator.add(f"check_failed:{d.table}")
        elif d.issue in ("terms_mismatch", "term_names_missing"):
            repairable.add("terms")
        elif d.issue:
            repairable.add(d.table)
    if any(not v.present for v in result.views):
        repairable.add("views")
    return repairable, operator


async def _list_namespaces() -> list[str]:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{settings.registry_url}/api/registry/namespaces",
            headers={"X-API-Key": settings.api_key},
            timeout=30.0,
        )
        response.raise_for_status()
        return [ns["prefix"] for ns in response.json()]


class ParityMonitor:
    def __init__(
        self,
        pool: asyncpg.Pool,
        batch_sync: BatchSyncService,
        *,
        interval_seconds: int | None = None,
        repair_attempts: int | None = None,
        list_namespaces: Callable[[], Awaitable[list[str]]] = _list_namespaces,
    ):
        self.pool = pool
        self.batch_sync = batch_sync
        self.interval = (
            settings.parity_check_interval_seconds if interval_seconds is None else interval_seconds
        )
        self.max_attempts = (
            settings.parity_repair_attempts if repair_attempts is None else repair_attempts
        )
        self._list_namespaces = list_namespaces
        self._status = ParityMonitorStatus(
            enabled=self.interval > 0,
            interval_seconds=self.interval,
            repair_attempts=self.max_attempts,
        )
        self._actions: deque[ParityAction] = deque(maxlen=200)
        # Gaps seen on the previous pass, per namespace — a repair needs a
        # second sighting.
        self._seen: dict[str, set[str]] = {}
        # Gaps already reported at error level, so a persistent one is not
        # logged every pass.
        self._reported: dict[str, set[str]] = {}

    def status(self) -> ParityMonitorStatus:
        return self._status.model_copy(update={"recent_actions": list(self._actions)})

    async def run_forever(self) -> None:
        """Pass after pass, the first one an interval after start-up, so the
        start-up backfill has its head start."""
        if self.interval <= 0:
            logger.info("Standing parity check disabled (PARITY_CHECK_INTERVAL_SECONDS=0)")
            return
        while True:
            await asyncio.sleep(self.interval)
            await self.run_once()

    async def run_once(self) -> None:
        self._status.last_pass_started_at = datetime.now(UTC)
        self._status.last_pass_error = None
        try:
            namespaces = await self._list_namespaces()
        except Exception as e:  # registry away: try again next pass
            self._status.last_pass_error = f"namespace list unavailable: {e}"
            logger.warning(f"Parity check pass skipped: {self._status.last_pass_error}")
            return
        for namespace in namespaces:
            try:
                await self.check_namespace(namespace)
            except Exception as e:  # one namespace must never stop the pass
                ns_status = self._status.namespaces.setdefault(namespace, NamespaceParityStatus())
                ns_status.checked_at, ns_status.error = datetime.now(UTC), str(e)
                logger.warning(f"Parity check of {namespace} failed: {e}")
        # Namespaces gone from the registry drop out of the status.
        for gone in set(self._status.namespaces) - set(namespaces):
            self._status.namespaces.pop(gone, None)
            self._seen.pop(gone, None)
            self._reported.pop(gone, None)
        self._status.last_pass_finished_at = datetime.now(UTC)

    def _sync_active(self, namespace: str) -> bool:
        return any(
            job.status in (BatchSyncStatus.PENDING, BatchSyncStatus.RUNNING)
            and job.namespace in (None, namespace)
            for job in self.batch_sync.list_jobs()
        )

    async def check_namespace(self, namespace: str) -> None:
        ns_status = self._status.namespaces.setdefault(namespace, NamespaceParityStatus())
        ns_status.checked_at = datetime.now(UTC)
        ns_status.error = None
        if self._sync_active(namespace):
            # The sync in flight owns the namespace; a gap it is closing is
            # not a gap to repair. Forget sightings so the next pass starts
            # its two-pass count afresh.
            ns_status.skipped = "batch sync active"
            self._seen.pop(namespace, None)
            return
        ns_status.skipped = None

        result = await check_namespace_parity(self.pool, namespace, include_counts=True)
        repairable, operator = _gaps(result)
        current = repairable | operator
        ns_status.ok = result.ok
        ns_status.gaps = sorted(current)

        previous = self._seen.get(namespace, set())
        self._seen[namespace] = current
        reported = self._reported.setdefault(namespace, set())
        # A closed gap forgets its repairs and its report.
        for key in list(ns_status.repair_attempts):
            if key not in current:
                del ns_status.repair_attempts[key]
        reported &= current

        for key in sorted(operator - reported):
            logger.error(
                f"Reporting parity gap in {namespace} needs an operator: {key} "
                "(no automatic repair) — see GET /api/reporting-sync/parity"
                f"?namespace={namespace}"
            )
            reported.add(key)

        confirmed = repairable & previous
        ran: set[str] = set()
        templates = sorted(k for k in confirmed if k.startswith("template:"))
        for key in [k for k in _ORDER if k in confirmed] + templates:
            if key == "term_closure" and ran & {"terms", "term_relations"}:
                continue  # those syncs end with a closure rebuild
            attempts = ns_status.repair_attempts.get(key, 0)
            if attempts >= self.max_attempts:
                if key not in reported:
                    logger.error(
                        f"Reporting parity gap in {namespace} not healed after "
                        f"{attempts} repair(s): {key} — see GET "
                        f"/api/reporting-sync/parity?namespace={namespace}"
                    )
                    reported.add(key)
                continue
            ns_status.repair_attempts[key] = attempts + 1
            await self._repair(namespace, key, attempts + 1)
            ran.add(key)

        ns_status.not_healing = sorted(reported)

    async def _repair(self, namespace: str, key: str, attempt: int) -> None:
        sm = self.batch_sync.schema_manager
        repairs: dict[str, tuple[str, Callable[[], Awaitable[Any]]]] = {
            "terms": ("term batch sync", lambda: self.batch_sync.batch_sync_terms(namespace=namespace)),
            "term_relations": (
                "term-relation batch sync",
                lambda: self.batch_sync.batch_sync_term_relations(namespace=namespace),
            ),
            "term_closure": ("closure rebuild", lambda: self.batch_sync.closure.rebuild(namespace)),
            "views": ("registered views re-applied", lambda: _recreate_views(sm, namespace)),
        }
        if key.startswith("template:"):
            value = key.split(":", 1)[1]
            repairs[key] = (
                "template batch sync",
                lambda: self.batch_sync.start_batch_sync(value, namespace=namespace),
            )
        label, run = repairs[key]
        action = ParityAction(
            at=datetime.now(UTC), namespace=namespace, gap=key, action=label, attempt=attempt
        )
        try:
            await run()
            logger.warning(
                f"Reporting parity gap in {namespace} repaired ({label}, "
                f"attempt {attempt}/{self.max_attempts}): {key}"
            )
        except Exception as e:
            action.error = str(e)
            logger.error(f"Reporting parity repair of {namespace}/{key} failed: {e}")
        self._actions.append(action)


async def _recreate_views(sm: SchemaManager, namespace: str) -> None:
    _, failed = await sm.recreate_registered_views(namespace)
    if failed:
        raise RuntimeError(f"{failed} registered view(s) could not be recreated")
