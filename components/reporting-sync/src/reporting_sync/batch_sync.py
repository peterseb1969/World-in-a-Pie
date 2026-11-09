"""
Batch Sync Service - Bulk sync documents from Document Store to PostgreSQL.

Responsibilities:
- Fetch documents from Document Store API (paginated)
- Transform and upsert into PostgreSQL
- Track progress and handle errors
- Support initial population and recovery scenarios
"""

import asyncio
import json
import logging
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import asyncpg
import httpx

from .config import settings
from .models import (
    BatchSyncJob,
    BatchSyncStatus,
    ReportingConfig,
)
from .schema_manager import SchemaManager
from .term_closure import TermClosureMaintainer
from .transformer import DocumentTransformer, _parse_datetime, term_name_rows

logger = logging.getLogger(__name__)


def _name_row_key(terminology_id: str | None, row: dict[str, Any]) -> tuple:
    """A term_names row as compared between def-store and the table."""
    return (terminology_id, row["value"], row["type"], row["language"],
            row["source"], row["preferred"])

# The smallest MongoDB ObjectId: a cursor below every document, so the first
# cursor page starts at the beginning. The document-store enters cursor mode
# only when a cursor is given.
FIRST_CURSOR = "000000000000000000000000"


def _only_if_changed(columns: tuple[str, ...]) -> str:
    """The WHERE of an upsert's DO UPDATE that skips an unchanged row.

    The batch sweeps re-send every terminology, term and relation, and a
    plain DO UPDATE rewrites each one: a new row version and a dead tuple per
    row even when nothing changed. Comparing the updated columns against the
    current row (aliased ``cur``) leaves an unchanged row alone.
    """
    current = ", ".join(f'cur."{c}"' for c in columns)
    incoming = ", ".join(f'EXCLUDED."{c}"' for c in columns)
    return f"WHERE ({current}) IS DISTINCT FROM ({incoming})"


_TERMINOLOGY_UPDATE_COLUMNS = (
    "value", "label", "description", "case_sensitive", "allow_multiple",
    "mutable", "status", "term_count", "updated_at", "updated_by",
)
_TERM_UPDATE_COLUMNS = (
    "terminology_value", "value", "aliases", "label", "description",
    "sort_order", "parent_term_id", "status", "updated_at", "updated_by",
)
_TERM_RELATION_UPDATE_COLUMNS = (
    "status", "source_term_value", "target_term_value", "metadata",
)


@dataclass
class _BatchRun:
    """One template's batch sync job in progress: what it syncs, and what it
    has learned about the tables so far."""

    job: BatchSyncJob
    template: dict[str, Any]
    config: ReportingConfig
    latest_version: int
    strategy: str
    # Every template version the job has read, by version number.
    version_templates: dict[int, dict[str, Any]] = field(default_factory=dict)
    # The table ensured for each (namespace, template_version).
    ns_tables: dict[tuple[str, int], str] = field(default_factory=dict)
    touched_namespaces: set[str] = field(default_factory=set)
    ns_versions_on_disk: dict[str, set[int]] = field(default_factory=dict)


@dataclass
class _TermSync:
    """A batch term sync in progress: its scope, the tables of each
    namespace, and its counts. complete is False once a listing failed: a
    sync that never listed cannot be told apart from an empty namespace by
    its counts alone."""

    namespace: str | None
    # Rows route to their own namespace's schema.
    ns_tables: dict[str, str] = field(default_factory=dict)
    ns_names_tables: dict[str, str] = field(default_factory=dict)
    synced: int = 0
    failed: int = 0
    complete: bool = True

    def term_namespace(self, term: dict[str, Any]) -> str:
        return term.get("namespace") or self.namespace or "wip"


# Finished jobs kept in memory for GET /sync/batch/jobs; older ones are
# forgotten as new jobs start.
MAX_FINISHED_JOBS = 200
_FINISHED_STATUSES = (
    BatchSyncStatus.COMPLETED, BatchSyncStatus.FAILED, BatchSyncStatus.CANCELLED,
)


class BatchSyncService:
    """Service for batch syncing documents to PostgreSQL."""

    def __init__(self, postgres_pool: asyncpg.Pool):
        self.pool = postgres_pool
        self.schema_manager = SchemaManager(postgres_pool)
        self.closure = TermClosureMaintainer(postgres_pool, self.schema_manager)
        self._jobs: dict[str, BatchSyncJob] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        # Per template value (lowercased): see _run_batch_sync.
        self._table_locks: dict[str, asyncio.Lock] = {}
        # Strong refs to fire-and-forget tasks (definitions pre-sync) so the
        # event loop cannot garbage-collect them mid-flight.
        self._background_tasks: set[asyncio.Task] = set()

    async def _fetch_template(self, template_id: str) -> dict[str, Any] | None:
        """Fetch template from Template Store."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates/{template_id}",
                    headers={"X-API-Key": settings.api_key},
                    timeout=30.0,
                )
                if response.status_code == 200:
                    return cast(dict[str, Any] | None, response.json())
                logger.error(f"Failed to fetch template {template_id}: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching template {template_id}: {e}")
            return None

    async def _fetch_template_by_value(
        self, template_value: str, namespace: str | None = None
    ) -> dict[str, Any] | None:
        """Fetch template by value from Template Store.

        A value is unique only within a namespace — without `namespace` the
        lookup searches all accessible namespaces and returns an arbitrary
        match when the value is shared (guaranteed after a remap restore).
        Pass it whenever the caller knows which namespace it means.
        """
        try:
            params: dict[str, str] = {}
            if namespace:
                params["namespace"] = namespace
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates/by-value/{template_value}",
                    params=params,
                    headers={"X-API-Key": settings.api_key},
                    timeout=30.0,
                )
                if response.status_code == 200:
                    return cast(dict[str, Any] | None, response.json())
                logger.error(f"Failed to fetch template by code {template_value}: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching template by code {template_value}: {e}")
            return None

    async def _fetch_latest_active_version(
        self, template_value: str, namespace: str
    ) -> dict[str, Any] | None:
        """The highest ACTIVE version of a template value in a namespace —
        the version a new write would land on.

        Deliberately NOT the by-id or by-value GET: both return the highest
        version REGARDLESS of status, which is the wrong shape for the
        eager table-ensure — with the newest version deactivated (a normal
        state: docs stay pinned to it), they hand back a version no new
        write can target, and the structural parity gate demands the
        latest-ACTIVE table instead. A restore once halted on exactly that
        mismatch: the eager ensure built the inactive version's table while
        the gate polled 30s for the active one's.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates",
                    params={
                        "namespace": namespace, "value": template_value,
                        "status": "active", "page_size": 1,
                    },
                    headers={"X-API-Key": settings.api_key},
                    timeout=30.0,
                )
                if response.status_code != 200:
                    logger.error(
                        f"Failed to fetch active version of {template_value}: "
                        f"{response.status_code}"
                    )
                    return None
                items = response.json().get("items", [])
        except Exception as e:
            logger.error(f"Error fetching active version of {template_value}: {e}")
            return None
        # The value branch sorts version-descending: first = latest active.
        if not items:
            return None
        row = cast(dict[str, Any], items[0])
        if not row.get("extends"):
            return row
        # The listing serves templates as stored; a template that extends
        # another is re-read through the by-id route, which resolves it.
        return await self._fetch_template_version(row["template_id"], int(row["version"]))

    async def _fetch_template_version(
        self, template_id: str, version: int
    ) -> dict[str, Any] | None:
        """Fetch one exact template version — the shape a version-pinned
        document validated against. Works for deactivated versions too (an
        explicit-version read is status-independent)."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates/{template_id}",
                    params={"version": str(version)},
                    headers={"X-API-Key": settings.api_key},
                    timeout=30.0,
                )
                if response.status_code == 200:
                    return cast(dict[str, Any] | None, response.json())
                logger.error(
                    f"Failed to fetch template {template_id} v{version}: "
                    f"{response.status_code}"
                )
                return None
        except Exception as e:
            logger.error(f"Error fetching template {template_id} v{version}: {e}")
            return None

    async def _list_templates(self) -> list[dict[str, Any]]:
        """List all templates from Template Store."""
        templates = []
        page = 1
        page_size = 100

        try:
            async with httpx.AsyncClient() as client:
                while True:
                    response = await client.get(
                        f"{settings.template_store_url}/api/template-store/templates",
                        params={"page": page, "page_size": page_size, "latest_only": "true"},
                        headers={"X-API-Key": settings.api_key},
                        timeout=30.0,
                    )
                    if response.status_code != 200:
                        logger.error(f"Failed to list templates: {response.status_code}")
                        break

                    data = response.json()
                    templates.extend(data.get("items", []))

                    if len(data.get("items", [])) < page_size:
                        break
                    page += 1

        except Exception as e:
            logger.error(f"Error listing templates: {e}")

        return templates

    async def _fetch_documents(
        self,
        template_id: str,
        page: int,
        page_size: int,
        status: str = "active",
        namespace: str | None = None,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """
        Fetch documents from Document Store.

        `namespace` scopes to that namespace's documents. The filter is on
        the DOCUMENT, not the template: a document may be based on a
        template owned by another namespace, so resolving the template
        does not scope its documents.

        With `cursor`, the document-store pages by `_id` (no sort over the
        whole template, no skip, no count; `page` is ignored and the total
        comes back as -1). Without it, offset paging — which sorts every
        matching document on every page.

        Returns:
            Tuple of (documents, total_count, next_cursor)
        """
        try:
            params: dict[str, Any] = {
                "template_id": template_id,
                "status": status,
                "page": page,
                "page_size": page_size,
            }
            if cursor:
                params["cursor"] = cursor
            if namespace:
                params["namespace"] = namespace
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{settings.document_store_url}/api/document-store/documents",
                    params=params,
                    headers={"X-API-Key": settings.api_key},
                    timeout=60.0,
                )
                if response.status_code == 200:
                    data = response.json()
                    return (
                        data.get("items", []),
                        data.get("total", 0),
                        data.get("next_cursor"),
                    )
                logger.error(f"Failed to fetch documents: {response.status_code}")
                return [], 0, None
        except Exception as e:
            logger.error(f"Error fetching documents: {e}")
            return [], 0, None

    def _get_reporting_config(self, template: dict[str, Any]) -> ReportingConfig:
        """Extract reporting config from template."""
        reporting_data = template.get("reporting", {})
        return ReportingConfig(**reporting_data) if reporting_data else ReportingConfig()

    def _find_active_job(
        self, template_id: str, namespace: str | None
    ) -> BatchSyncJob | None:
        """Find an active (pending/running) job whose scope OVERLAPS.

        Overlap, not equality: a whole-instance job (namespace=None) writes
        every namespace's tables, so it conflicts with any scoped job for
        the same template, and vice versa. Two concurrent writers on one
        table interleave upserts (and, under latest_only, sibling-version
        deletes) from different page cursors — last-writer-wins ordering is
        undefined. Dedup is a correctness guard, not just waste avoidance.
        A DIFFERENT template that shares the value writes the same tables
        too; that job is not a duplicate, so it is not deduplicated but
        serialised (_table_lock).
        """
        for job in self._jobs.values():
            if job.template_id != template_id:
                continue
            if job.status not in (BatchSyncStatus.PENDING, BatchSyncStatus.RUNNING):
                continue
            if job.namespace is None or namespace is None or job.namespace == namespace:
                return job
        return None

    async def start_batch_sync(
        self,
        template_value: str,
        force: bool = False,
        page_size: int = 1000,
        namespace: str | None = None,
        template: dict[str, Any] | None = None,
    ) -> BatchSyncJob:
        """
        Start a batch sync job for a template.

        If an active job with an overlapping scope already exists for the
        same template, that job is returned instead of starting a second
        concurrent writer — the trigger is idempotent, and the returned
        job is marked ``deduplicated`` so the caller can see it joined an
        existing run. This holds for force too: force never cancels an
        active job (the dedup is a correctness guard against concurrent
        writers on one table) — cancel the active job first, then
        re-trigger.

        Args:
            template_value: Template code to sync
            force: Drop the template's existing reporting relations in the
                target namespace (version tables, entity views, and any
                legacy pre-split table) before syncing — rebuild from
                source. Requires an explicit ``namespace``: table names
                derive from the template value, so an instance-wide drop
                could destroy same-valued foreign templates' tables that
                this job would never rebuild.
            page_size: Number of documents to fetch per page
            namespace: Scope the sync to this namespace's documents
                (None = all namespaces). Also disambiguates the template
                lookup when the value exists in several namespaces.
            template: Pre-fetched template dict (batch-all passes the
                listed template so each job binds to an exact template_id
                instead of re-resolving by value)

        Returns:
            BatchSyncJob with job status
        """
        if force and namespace is None:
            raise ValueError(
                "force=true requires an explicit namespace — the "
                "drop-and-rebuild is namespace-scoped"
            )

        if template is None:
            template = await self._fetch_template_by_value(template_value, namespace)

        if template is not None:
            existing = self._find_active_job(template["template_id"], namespace)
            if existing is not None:
                existing.deduplicated = True
                return existing

        job_id = str(uuid.uuid4())[:8]
        job = BatchSyncJob(
            job_id=job_id,
            template_value=template_value,
            template_id=template.get("template_id") if template else None,
            namespace=namespace,
            status=BatchSyncStatus.PENDING,
        )
        self._forget_old_finished_jobs()
        self._jobs[job_id] = job

        if template is None:
            # Preserve the async-error contract: an unknown template is a
            # FAILED job, not an HTTP error from the trigger.
            job.status = BatchSyncStatus.FAILED
            job.error_message = f"Template {template_value} not found"
            job.completed_at = datetime.now(UTC)
            return job

        # Start async task
        task = asyncio.create_task(
            self._run_batch_sync(job, template, force, page_size)
        )
        self._running_tasks[job_id] = task

        return job

    async def _run_batch_sync(
        self,
        job: BatchSyncJob,
        template: dict[str, Any],
        force: bool,
        page_size: int,
    ) -> None:
        """Run the batch sync job.

        Reporting tables are named from the template VALUE inside the
        documents' namespace schema, so two same-valued templates (one
        shared into the other's namespace) write the same tables. Their jobs
        run one after the other: the job stays pending until the value's
        lock is free.
        """
        try:
            async with self._table_lock(template.get("value") or job.template_value):
                job.status = BatchSyncStatus.RUNNING
                job.started_at = datetime.now(UTC)
                await self._sync_template_documents(job, template, force, page_size)
        except asyncio.CancelledError:
            job.status = BatchSyncStatus.CANCELLED
            job.completed_at = datetime.now(UTC)
            logger.info(f"Batch sync cancelled for {job.template_value}")

        except Exception as e:
            job.status = BatchSyncStatus.FAILED
            job.error_message = str(e)
            job.completed_at = datetime.now(UTC)
            logger.error(f"Batch sync failed for {job.template_value}: {e}", exc_info=True)

    async def _sync_template_documents(
        self,
        job: BatchSyncJob,
        template: dict[str, Any],
        force: bool,
        page_size: int,
    ) -> None:
        """Sync every document of the template in the job's scope: ensure
        its tables, upsert its documents page by page, rebuild its views."""
        template = await self._resolved_template(template)
        config = self._get_reporting_config(template)
        if not config.sync_enabled:
            job.status = BatchSyncStatus.COMPLETED
            job.error_message = "Sync disabled for this template"
            job.completed_at = datetime.now(UTC)
            return

        run = _BatchRun(
            job=job, template=template, config=config,
            latest_version=int(template.get("version", 1)),
            strategy=config.sync_strategy.value,
        )
        run.version_templates[run.latest_version] = template
        tpl_table = await self._prepare_template_tables(run, force)

        # The total, for progress and the empty-template exit: one
        # one-document offset request (a count plus a single sort).
        # The pages themselves are fetched by cursor.
        fetch_started = time.monotonic()
        _probe, total, _ = await self._fetch_documents(
            template["template_id"], 1, 1, namespace=job.namespace
        )
        job.fetch_ms += int((time.monotonic() - fetch_started) * 1000)
        job.total_documents = total

        if total == 0:
            job.status = BatchSyncStatus.COMPLETED
            job.completed_at = datetime.now(UTC)
            detail = (
                f"(table {tpl_table} ensured empty)" if tpl_table
                else "(foreign template — no table created in scope)"
            )
            logger.info(f"No documents to sync for {job.template_value} {detail}")
            return

        logger.info(f"Starting batch sync for {job.template_value}: {total} documents")
        await self._sync_pages(run, page_size)
        await self._rebuild_views(run)

        job.status = BatchSyncStatus.COMPLETED
        job.completed_at = datetime.now(UTC)
        logger.info(
            f"Batch sync completed for {job.template_value}: "
            f"{job.documents_synced} synced, {job.documents_failed} failed"
        )

    async def _resolved_template(self, template: dict[str, Any]) -> dict[str, Any]:
        """The template as the job syncs it.

        A template from a listing is served as stored; one that extends
        another is re-read through Template Store's resolving route, the one
        resolver every service uses (pinned parent version, child's own
        properties kept, a broken chain refused).
        """
        if not template.get("extends"):
            return template
        resolved = await self._fetch_template_version(
            template["template_id"], int(template.get("version", 1))
        )
        if resolved is None:
            raise RuntimeError(
                f"Template {template.get('value')} v{template.get('version')} "
                "could not be read resolved from Template Store"
            )
        return resolved

    async def _template_for_version(self, run: _BatchRun, version: int) -> dict[str, Any]:
        """The template version a document validated against.

        Documents route to their own namespace's schema AND to their pinned
        template version's table (per-version split) — a single template can
        fill several tables per namespace. Each document is transformed
        against the template version it validated against
        (pin-to-what-validated), never against latest — that mismatch was
        the NULL-conflation the split eliminates.
        """
        if version in run.version_templates:
            return run.version_templates[version]
        fetched = await self._fetch_template_version(run.template["template_id"], version)
        if fetched is None:
            # Pinned version unavailable — fall back to the latest
            # definition, loudly: better a possibly-wider table than
            # dropping the document.
            logger.warning(
                f"Template {run.job.template_value} v{version} not "
                f"fetchable; falling back to v{run.latest_version}"
            )
            fetched = run.template
        run.version_templates[version] = fetched
        return fetched

    async def _versions_on_disk(self, run: _BatchRun, ns: str) -> set[int]:
        """The namespace's physical version tables of the template.

        One catalog query per namespace per job, kept current with tables
        this job ensures. The per-document sibling cleanup consults this
        instead of re-asking information_schema for every document — at
        restore scale that was one catalog query per document, almost always
        to learn there are no siblings at all.
        """
        if ns not in run.ns_versions_on_disk:
            run.ns_versions_on_disk[ns] = set(
                (await self.schema_manager.list_version_tables(
                    ns, run.job.template_value, run.config
                )).keys()
            )
        return run.ns_versions_on_disk[ns]

    async def _prepare_template_tables(self, run: _BatchRun, force: bool) -> str | None:
        """Drop the template's relations when forced, and ensure its latest
        active version's table eagerly; the table ensured, if any.

        Both only when the template belongs to the job's scope. Eagerly
        ensuring even with zero documents: a freshly bootstrapped namespace
        (templates, no docs yet) must be SQL-queryable — "no rows yet" is an
        empty table (and view), not relation-does-not-exist. But a scoped run
        iterates the instance-wide template list, and eagerly ensuring
        foreign templates would stamp every namespace's empty tables into the
        target schema (this shipped once — a namespace grew 31 foreign
        tables). Foreign-template tables are created lazily, only when scoped
        documents actually exist.
        """
        job = run.job
        tpl_ns = run.template.get("namespace") or "wip"

        # Force = drop-and-rebuild: remove the template's existing relations
        # in the target namespace before the sync recreates them from current
        # template shapes. Upserts cannot heal mis-shaped DDL (a table created
        # from a same-valued foreign template rejects the namespace's own
        # documents); only drop-and-recreate can. Guarded to templates that
        # belong to the job's scope: table names derive from the template
        # VALUE, so a foreign same-valued template's job dropping here would
        # race the owning template's rebuild of the identically-named tables.
        if force and job.namespace is not None and job.namespace == tpl_ns:
            job.dropped_relations = (
                await self.schema_manager.drop_relations_for_template(
                    tpl_ns, job.template_value, run.config
                )
            )

        if job.namespace is not None and job.namespace != tpl_ns:
            return None
        eager = await self._eager_template(run, tpl_ns)
        if eager is None:
            return None
        eager_template, eager_version = eager
        # Uncached: batch sync is the repair path, and runs the catalog
        # checks once per job, not per document.
        tpl_table = await self.schema_manager.ensure_table_for_template(
            tpl_ns, eager_template, cached=False
        )
        if tpl_table:
            run.ns_tables[(tpl_ns, eager_version)] = tpl_table
            run.touched_namespaces.add(tpl_ns)
        return tpl_table

    async def _eager_template(
        self, run: _BatchRun, tpl_ns: str,
    ) -> tuple[dict[str, Any], int] | None:
        """The template version to ensure eagerly, and its number: the latest
        ACTIVE version, or None when the template has no active version.

        That is the version a new write lands on, and the version the restore
        gate's structural parity demands. The template in hand may be an
        INACTIVE newest version (the instance-wide listing is deliberately
        status-free, so fully-deactivated templates' documents still sync);
        ensuring ITS table while parity waits for the active one's halted a
        restore at the 30s gate. Documents pinned to inactive versions are
        unaffected: their tables materialize lazily per page. Absent status
        means active (the platform default) — only a template explicitly
        deactivated triggers the re-fetch. With no active version at all,
        parity's active-only listing skips this template too; nothing to
        ensure eagerly, the lazy path covers its documents.
        """
        if run.template.get("status", "active") == "active":
            return run.template, run.latest_version
        fetched_active = await self._fetch_latest_active_version(
            run.job.template_value, tpl_ns
        )
        if fetched_active is None:
            return None
        version = int(fetched_active.get("version", 1))
        run.version_templates[version] = fetched_active
        return fetched_active, version

    async def _sync_pages(self, run: _BatchRun, page_size: int) -> None:
        """Sync the documents page by page, by cursor.

        Offset paging sorted every matching document on every page (the
        default order cannot use an index), so a template's sync cost grew
        with the square of its size; a cursor page reads one page of the
        ns_template_status_id_idx index. The smallest possible _id starts the
        walk, and the document-store returns no next cursor after a short
        page, which ends it.
        """
        job = run.job
        page = 0
        cursor: str | None = FIRST_CURSOR
        while cursor is not None:
            page += 1
            fetch_started = time.monotonic()
            documents, _, cursor = await self._fetch_documents(
                run.template["template_id"], page, page_size, namespace=job.namespace,
                cursor=cursor,
            )
            job.fetch_ms += int((time.monotonic() - fetch_started) * 1000)
            if not documents:
                break
            job.current_page = page
            await self._ensure_page_tables(run, documents)
            await self._upsert_page(run, documents)
            if run.strategy == "latest_only":
                await self._clean_sibling_tables(run, documents)
            # Yield to the event loop between pages. A flat 100 ms sleep
            # used to sit here; it paced nothing (no job concurrency cap
            # exists) and cost ~2.6 min of pure sleep on a 156k-document sync.
            await asyncio.sleep(0)

    @staticmethod
    def _document_table_key(run: _BatchRun, document: dict[str, Any]) -> tuple[str, int]:
        """(namespace, template_version) of a document's table."""
        return (
            document.get("namespace") or "wip",
            int(document.get("template_version", run.latest_version)),
        )

    async def _ensure_page_tables(self, run: _BatchRun, documents: list[dict[str, Any]]) -> None:
        """Ensure a table for each (namespace, template_version) present in
        the page. ensure_table_for_template uses its own pooled connection,
        so this runs before a connection is held for the upserts."""
        for document in documents:
            key = self._document_table_key(run, document)
            if key in run.ns_tables:
                continue
            ns, doc_tv = key
            version_template = await self._template_for_version(run, doc_tv)
            run.ns_tables[key] = await self.schema_manager.ensure_table_for_template(
                ns, version_template, cached=False
            )
            run.touched_namespaces.add(ns)
            # Keep the sibling-table knowledge current with the table this
            # job just ensured.
            (await self._versions_on_disk(run, ns)).add(doc_tv)

    async def _upsert_page(self, run: _BatchRun, documents: list[dict[str, Any]]) -> None:
        """Upsert the page's documents, each routed to its (schema, version
        table) and transformed against the template version it validated
        against. A document that fails is counted and logged; the page goes
        on."""
        job = run.job
        upsert_started = time.monotonic()
        async with self.pool.acquire() as conn:
            for document in documents:
                try:
                    key = self._document_table_key(run, document)
                    table_name = run.ns_tables[key]
                    version_template = await self._template_for_version(run, key[1])
                    transformer = DocumentTransformer(run.config)
                    for row in transformer.transform(document, version_template):
                        sql, values = transformer.generate_upsert_sql(
                            table_name, row, run.strategy
                        )
                        await conn.execute(sql, *values)
                        job.rows_written += 1
                    job.documents_synced += 1
                except Exception as e:
                    logger.error(
                        f"Error syncing document {document.get('document_id')}: {e}"
                    )
                    job.documents_failed += 1
        job.upsert_ms += int((time.monotonic() - upsert_started) * 1000)

    async def _clean_sibling_tables(self, run: _BatchRun, documents: list[dict[str, Any]]) -> None:
        """Remove the page's documents from their sibling version tables.

        Version-crossing upserts leave stale rows in sibling version tables
        under latest_only — the page's upserts are paired with the sibling
        cleanup per document. Only when a sibling table actually exists,
        though: the common case (single version table, every restore) has
        nothing to clean, and consulting the per-job knowledge instead of the
        catalog keeps the check free per document. A sibling table created by
        a concurrent writer mid-job is outside this knowledge — that writer
        pairs its own upserts with its own cleanup, so nothing is orphaned by
        skipping here.
        """
        sibling_started = time.monotonic()
        for document in documents:
            ns, doc_tv = self._document_table_key(run, document)
            doc_id = document.get("document_id")
            if doc_id and ((await self._versions_on_disk(run, ns)) - {doc_tv}):
                await self.schema_manager.delete_from_sibling_version_tables(
                    ns, run.job.template_value, run.config,
                    keep_version=doc_tv, document_id=doc_id,
                )
        run.job.sibling_ms += int((time.monotonic() - sibling_started) * 1000)

    async def _rebuild_views(self, run: _BatchRun) -> None:
        """Rebuild the entity views for every namespace the job touched — the
        union membership may have grown with lazily created version tables.
        A legacy pre-split table is reported on the job, never auto-dropped."""
        job = run.job
        for ns in run.touched_namespaces:
            warning = await self.schema_manager.ensure_views_for_template(
                ns, job.template_value, run.config
            )
            if warning:
                job.error_message = (
                    (job.error_message + "; " if job.error_message else "") + warning
                )

    async def _sync_definitions(
        self, namespace: str | None, page_size: int
    ) -> None:
        """Template-metadata, terminology, term and term-relation sync, run
        as a background companion of batch-all: the same four sweeps as the
        startup backfill. Not a dependency of the document jobs: the
        document upsert path transforms the document JSON against its
        template and never reads the definitions tables. Relations run after
        terms, so the closure rebuild that ends the relation sync sees every
        term. A sweep that failed items or could not list everything is
        logged as a warning: nothing else reports it."""
        sweeps = (
            ("Template metadata", self.batch_sync_templates),
            ("Terminologies", self.batch_sync_terminologies),
            ("Terms", self.batch_sync_terms),
            ("Term relations", self.batch_sync_term_relations),
        )
        for label, sweep in sweeps:
            logger.info(f"Batch syncing {label.lower()}...")
            result = await sweep(namespace=namespace, page_size=page_size)
            if result.get("failed") or not result.get("complete", True):
                logger.warning(
                    f"{label} sync incomplete: {result['failed']} failed, "
                    f"listing complete: {result.get('complete')}"
                )
            else:
                logger.info(f"{label}: {result}")

    async def start_batch_sync_all(
        self,
        force: bool = False,
        page_size: int = 1000,
        namespace: str | None = None,
    ) -> list[BatchSyncJob]:
        """
        Start batch sync for all templates.

        With `namespace`, every job is scoped to that namespace's DOCUMENTS
        — but the template list stays instance-wide, because a document may
        be based on a template owned by another namespace (verified: the
        create path accepts a foreign template by UUID or qualified value).
        Scoping the list to the namespace's own templates would silently
        miss those documents; a foreign-template job with no documents in
        the namespace completes after one empty page fetch instead.

        With `force` (requires `namespace`), each job whose template
        belongs to the namespace drops that template's existing reporting
        relations before rebuilding — the namespace-wide recovery path for
        schema-drift residue. Foreign-template jobs never drop (see
        _run_batch_sync); a residue table under a shared value is removed
        by the namespace's own same-valued template's job.

        The whole fan-out is acknowledgement-only: jobs (and the
        definitions pre-sync) run as background tasks, so the call returns
        after one template-list round-trip regardless of template count.

        Returns:
            List of BatchSyncJob for each template
        """
        # Terminologies + terms sync runs concurrently — the document jobs
        # do not read those tables, so there is no ordering dependency and
        # no reason to block the acknowledgement on it.
        definitions_task = asyncio.create_task(
            self._sync_definitions(namespace, page_size)
        )
        self._background_tasks.add(definitions_task)
        definitions_task.add_done_callback(self._background_tasks.discard)

        templates = await self._list_templates()
        jobs = []

        for template in templates:
            template_value = template.get("value")
            if not template_value:
                continue

            # Check if sync is enabled
            config = self._get_reporting_config(template)
            if not config.sync_enabled:
                logger.info(f"Skipping {template_value}: sync disabled")
                continue

            job = await self.start_batch_sync(
                template_value, force, page_size,
                namespace=namespace, template=template,
            )
            jobs.append(job)

        return jobs

    def get_job(self, job_id: str) -> BatchSyncJob | None:
        """Get a batch sync job by ID."""
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[BatchSyncJob]:
        """List all batch sync jobs."""
        return list(self._jobs.values())

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a running batch sync job."""
        task = self._running_tasks.get(job_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    async def batch_sync_terminologies(
        self,
        namespace: str | None = None,
        page_size: int = 100,
    ) -> dict:
        """
        Batch sync all terminologies from Def-Store to PostgreSQL.

        Returns:
            Dict with sync results (synced, failed, total)
        """
        # Rows route to their own namespace's schema (CASE-628).
        ns_tables: dict[str, str] = {}
        synced = 0
        failed = 0
        # False when a listing failed: a sync that never listed cannot be
        # told apart from an empty namespace by its counts alone.
        complete = True

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                page = 1

                while True:
                    params: dict = {"page": page, "page_size": page_size}
                    if namespace:
                        params["namespace"] = namespace
                    resp = await client.get(
                        f"{settings.def_store_url}/api/def-store/terminologies",
                        params=params,
                        headers={"X-API-Key": settings.api_key},
                    )
                    if resp.status_code != 200:
                        logger.error(f"Failed to list terminologies: {resp.status_code}")
                        complete = False
                        break

                    data = resp.json()
                    items = data.get("items", [])

                    if not items:
                        break

                    # Ensure a table per namespace present in this page.
                    for t in items:
                        ns = t.get("namespace") or namespace or "wip"
                        if ns not in ns_tables:
                            ns_tables[ns] = await self.schema_manager.ensure_terminologies_table(ns)

                    async with self.pool.acquire() as conn:
                        for t in items:
                            try:
                                table_name = ns_tables[t.get("namespace") or namespace or "wip"]
                                await conn.execute(
                                    f"""
                                    INSERT INTO {table_name} AS cur (
                                        "terminology_id", "namespace", "value", "label",
                                        "description", "case_sensitive", "allow_multiple",
                                        "mutable", "status", "term_count",
                                        "created_at", "created_by", "updated_at", "updated_by"
                                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                                    ON CONFLICT ("namespace", "terminology_id")
                                    DO UPDATE SET
                                        "value" = EXCLUDED."value",
                                        "label" = EXCLUDED."label",
                                        "description" = EXCLUDED."description",
                                        "case_sensitive" = EXCLUDED."case_sensitive",
                                        "allow_multiple" = EXCLUDED."allow_multiple",
                                        "mutable" = EXCLUDED."mutable",
                                        "status" = EXCLUDED."status",
                                        "term_count" = EXCLUDED."term_count",
                                        "updated_at" = EXCLUDED."updated_at",
                                        "updated_by" = EXCLUDED."updated_by"
                                    {_only_if_changed(_TERMINOLOGY_UPDATE_COLUMNS)}
                                    """,  # nosec B608  # identifiers validated by _safe_ident / ensure_*, values bound
                                    t["terminology_id"],
                                    t.get("namespace", namespace),
                                    t["value"],
                                    t.get("label"),
                                    t.get("description"),
                                    t.get("case_sensitive", False),
                                    t.get("allow_multiple", False),
                                    t.get("mutable", False),
                                    t.get("status", "active"),
                                    t.get("term_count", 0),
                                    _parse_datetime(t.get("created_at")),
                                    t.get("created_by"),
                                    _parse_datetime(t.get("updated_at")),
                                    t.get("updated_by"),
                                )
                                synced += 1
                            except Exception as e:
                                logger.error(f"Error syncing terminology {t.get('value')}: {e}")
                                failed += 1

                    if page >= data.get("pages", 1):
                        break
                    page += 1
                    await asyncio.sleep(0.05)

        except Exception as e:
            logger.error(f"Batch terminology sync error: {e}", exc_info=True)
            complete = False

        logger.info(f"Terminology batch sync: {synced} synced, {failed} failed")
        return {"synced": synced, "failed": failed, "total": synced + failed, "complete": complete}

    async def batch_sync_templates(
        self,
        namespace: str | None = None,
        page_size: int = 100,
    ) -> dict:
        """
        Batch sync all templates from Template-Store to PostgreSQL.

        Returns:
            Dict with sync results (synced, failed, total)
        """
        # Rows route to their own namespace's schema (CASE-628).
        ns_tables: dict[str, str] = {}
        synced = 0
        failed = 0
        # False when a listing failed: a sync that never listed cannot be
        # told apart from an empty namespace by its counts alone.
        complete = True

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                page = 1

                while True:
                    # latest_only: the templates table keys one row per
                    # (namespace, template_id) — the same row the live event
                    # path last wrote. Without it the list returns every
                    # version and the final upserted row depends on page
                    # ordering, not on which version is actually latest.
                    params: dict = {
                        "page": page, "page_size": page_size,
                        "latest_only": "true",
                    }
                    if namespace:
                        params["namespace"] = namespace
                    resp = await client.get(
                        f"{settings.template_store_url}/api/template-store/templates",
                        params=params,
                        headers={"X-API-Key": settings.api_key},
                    )
                    if resp.status_code != 200:
                        logger.error(f"Failed to list templates: {resp.status_code}")
                        complete = False
                        break

                    data = resp.json()
                    items = data.get("items", [])

                    if not items:
                        break

                    # Ensure a table per namespace present in this page.
                    for t in items:
                        ns = t.get("namespace") or namespace or "wip"
                        if ns not in ns_tables:
                            ns_tables[ns] = await self.schema_manager.ensure_templates_table(ns)

                    async with self.pool.acquire() as conn:
                        for t in items:
                            try:
                                table_name = ns_tables[t.get("namespace") or namespace or "wip"]
                                await conn.execute(
                                    f"""
                                    INSERT INTO {table_name} (
                                        "template_id", "namespace", "value", "label",
                                        "description", "version", "status", "extends",
                                        "extends_version", "created_at", "created_by",
                                        "updated_at", "updated_by"
                                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                                    ON CONFLICT ("namespace", "template_id")
                                    DO UPDATE SET
                                        "value" = EXCLUDED."value",
                                        "label" = EXCLUDED."label",
                                        "description" = EXCLUDED."description",
                                        "version" = EXCLUDED."version",
                                        "status" = EXCLUDED."status",
                                        "extends" = EXCLUDED."extends",
                                        "extends_version" = EXCLUDED."extends_version",
                                        "updated_at" = EXCLUDED."updated_at",
                                        "updated_by" = EXCLUDED."updated_by"
                                    """,  # nosec B608  # identifiers validated by _safe_ident / ensure_*, values bound
                                    t["template_id"],
                                    t.get("namespace", namespace),
                                    t["value"],
                                    t.get("label"),
                                    t.get("description"),
                                    t.get("version", 1),
                                    t.get("status", "active"),
                                    t.get("extends"),
                                    t.get("extends_version"),
                                    _parse_datetime(t.get("created_at")),
                                    t.get("created_by"),
                                    _parse_datetime(t.get("updated_at")),
                                    t.get("updated_by"),
                                )
                                synced += 1
                            except Exception as e:
                                logger.error(f"Error syncing template {t.get('value')}: {e}")
                                failed += 1

                    if page >= data.get("pages", 1):
                        break
                    page += 1
                    await asyncio.sleep(0.05)

        except Exception as e:
            logger.error(f"Batch template sync error: {e}", exc_info=True)
            complete = False

        logger.info(f"Template batch sync: {synced} synced, {failed} failed")
        return {"synced": synced, "failed": failed, "total": synced + failed, "complete": complete}

    async def batch_sync_terms(
        self,
        namespace: str | None = None,
        page_size: int = 1000,
    ) -> dict:
        """
        Batch sync all terms from Def-Store to PostgreSQL.

        Iterates through all terminologies and fetches their terms.

        Returns:
            Dict with sync results (synced, failed, total, complete)
        """
        run = _TermSync(namespace=namespace)
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                terminology_ids = await self._list_terminology_ids(client, run)
                logger.info(f"Found {len(terminology_ids)} terminologies, fetching terms...")
                for tidx, tid in enumerate(terminology_ids):
                    await self._sync_terminology_terms(client, run, tid, page_size)
                    if (tidx + 1) % 10 == 0:
                        logger.info(
                            f"Term sync progress: {tidx+1}/{len(terminology_ids)} terminologies, "
                            f"{run.synced} terms synced"
                        )
        except Exception as e:
            logger.error(f"Batch term sync error: {e}", exc_info=True)
            run.complete = False

        if run.synced:
            for ns in run.ns_tables:
                try:
                    await self.closure.rebuild(ns)
                except Exception as e:
                    logger.error(f"term_closure rebuild failed for {ns}: {e}")

        logger.info(f"Term batch sync: {run.synced} synced, {run.failed} failed")
        return {
            "synced": run.synced, "failed": run.failed,
            "total": run.synced + run.failed, "complete": run.complete,
        }

    async def _list_terminology_ids(self, client: httpx.AsyncClient, run: _TermSync) -> list[str]:
        """Every terminology id in scope, page by page. A failed listing
        marks the sync incomplete and returns what was listed."""
        terminology_ids: list[str] = []
        page = 1
        while True:
            params: dict = {"page": page, "page_size": 100}
            if run.namespace:
                params["namespace"] = run.namespace
            resp = await client.get(
                f"{settings.def_store_url}/api/def-store/terminologies",
                params=params,
                headers={"X-API-Key": settings.api_key},
            )
            if resp.status_code != 200:
                logger.error(f"Failed to list terminologies for terms: {resp.status_code}")
                run.complete = False
                return terminology_ids
            data = resp.json()
            terminology_ids += [t["terminology_id"] for t in data.get("items", [])]
            if page >= data.get("pages", 1):
                return terminology_ids
            page += 1

    async def _sync_terminology_terms(
        self, client: httpx.AsyncClient, run: _TermSync, tid: str, page_size: int,
    ) -> None:
        """Sync one terminology's terms, page by page. A failed listing marks
        the sync incomplete; an empty page ends the terminology."""
        page = 1
        while True:
            resp = await client.get(
                f"{settings.def_store_url}/api/def-store/terminologies/{tid}/terms",
                params={"page": page, "page_size": page_size},
                headers={"X-API-Key": settings.api_key},
            )
            if resp.status_code != 200:
                logger.error(f"Failed to list terms for {tid}: {resp.status_code}")
                run.complete = False
                return
            data = resp.json()
            items = data.get("items", [])
            if not items:
                return
            await self._sync_term_page(run, items)
            if page >= data.get("pages", 1):
                return
            page += 1
            await asyncio.sleep(0.05)

    async def _sync_term_page(self, run: _TermSync, items: list[dict[str, Any]]) -> None:
        """Ensure the tables of each namespace on the page, then upsert each
        term. A term that fails is counted and logged; the page goes on."""
        for t in items:
            ns = run.term_namespace(t)
            if ns not in run.ns_tables:
                run.ns_tables[ns] = await self.schema_manager.ensure_terms_table(ns)
            if ns not in run.ns_names_tables:
                run.ns_names_tables[ns] = await self.schema_manager.ensure_term_names_table(ns)

        async with self.pool.acquire() as conn:
            current_names = await self._current_name_rows(
                conn, items, run.namespace, run.ns_names_tables
            )
            for t in items:
                try:
                    await self._upsert_term(conn, run, t)
                    await self._write_term_names(conn, run, t, current_names)
                    run.synced += 1
                except Exception as e:
                    logger.error(f"Error syncing term {t.get('value')}: {e}")
                    run.failed += 1

    async def _upsert_term(self, conn: Any, run: _TermSync, t: dict[str, Any]) -> None:
        """Upsert one term's row; an unchanged row is left alone."""
        namespace = run.namespace
        table_name = run.ns_tables[run.term_namespace(t)]
        await conn.execute(
            f"""
            INSERT INTO {table_name} AS cur (
                "term_id", "namespace", "terminology_id",
                "terminology_value", "value", "aliases",
                "label", "description", "sort_order",
                "parent_term_id", "status",
                "deprecated_reason", "replaced_by_term_id",
                "created_at", "created_by", "updated_at", "updated_by"
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
            ON CONFLICT ("namespace", "term_id")
            DO UPDATE SET
                "terminology_value" = EXCLUDED."terminology_value",
                "value" = EXCLUDED."value",
                "aliases" = EXCLUDED."aliases",
                "label" = EXCLUDED."label",
                "description" = EXCLUDED."description",
                "sort_order" = EXCLUDED."sort_order",
                "parent_term_id" = EXCLUDED."parent_term_id",
                "status" = EXCLUDED."status",
                "updated_at" = EXCLUDED."updated_at",
                "updated_by" = EXCLUDED."updated_by"
            {_only_if_changed(_TERM_UPDATE_COLUMNS)}
            """,  # nosec B608  # identifiers validated by _safe_ident / ensure_*, values bound
            t["term_id"],
            t.get("namespace", namespace),
            t.get("terminology_id"),
            t.get("terminology_value"),
            t["value"],
            json.dumps(t.get("aliases", [])),
            t.get("label"),
            t.get("description"),
            t.get("sort_order", 0),
            t.get("parent_term_id"),
            t.get("status", "active"),
            t.get("deprecated_reason"),
            t.get("replaced_by_term_id"),
            _parse_datetime(t.get("created_at")),
            t.get("created_by"),
            _parse_datetime(t.get("updated_at")),
            t.get("updated_by"),
        )

    async def _write_term_names(
        self, conn: Any, run: _TermSync, t: dict[str, Any],
        current_names: dict[tuple[str, str], Counter],
    ) -> None:
        """Rewrite the term's name rows, only when they differ from what the
        table holds."""
        t_ns = run.term_namespace(t)
        names_table = run.ns_names_tables[t_ns]
        name_rows = term_name_rows(t)
        incoming = Counter(_name_row_key(t.get("terminology_id"), r) for r in name_rows)
        if incoming == current_names.get((t_ns, t["term_id"]), Counter()):
            return
        await conn.execute(
            f'DELETE FROM {names_table} WHERE "namespace" = $1 AND "term_id" = $2',  # nosec B608  # identifiers validated by _safe_ident / ensure_*, values bound
            t_ns, t["term_id"],
        )
        if name_rows:
            await conn.executemany(
                f"""
                INSERT INTO {names_table} (
                    "namespace", "term_id", "terminology_id",
                    "value", "type", "language", "source", "preferred"
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,  # nosec B608  # identifiers validated by _safe_ident / ensure_*, values bound
                [
                    (
                        t_ns, t["term_id"],
                        t.get("terminology_id"),
                        r["value"], r["type"], r["language"],
                        r["source"], r["preferred"],
                    )
                    for r in name_rows
                ],
            )

    @staticmethod
    async def _current_name_rows(
        conn: Any,
        items: list[dict[str, Any]],
        namespace: str | None,
        names_tables: dict[str, str],
    ) -> dict[tuple[str, str], Counter]:
        """The name rows the names tables hold for one page of terms.

        One query per namespace on the page, keyed by (namespace, term_id),
        each a multiset of rows in the shape _name_row_key builds.
        """
        by_ns: dict[str, list[str]] = {}
        for t in items:
            by_ns.setdefault(t.get("namespace") or namespace or "wip", []).append(t["term_id"])
        current: dict[tuple[str, str], Counter] = {}
        for ns, term_ids in by_ns.items():
            rows = await conn.fetch(
                f'''SELECT "term_id", "terminology_id", "value", "type", "language", "source", "preferred"
                FROM {names_tables[ns]} WHERE "namespace" = $1 AND "term_id" = ANY($2::text[])''',  # nosec B608  # identifiers validated by _safe_ident / ensure_*, values bound
                ns, term_ids,
            )
            for r in rows:
                current.setdefault((ns, r["term_id"]), Counter())[
                    (r["terminology_id"], r["value"], r["type"], r["language"],
                     r["source"], r["preferred"])
                ] += 1
        return current

    async def batch_sync_term_relations(
        self,
        namespace: str | None = None,
        page_size: int = 1000,
    ) -> dict:
        """
        Batch sync all term-relations from Def-Store to PostgreSQL.

        Uses the /ontology/term-relations/all endpoint for efficient pagination
        across all term_relations (no per-term iteration needed). With an
        explicit namespace, syncs that namespace only; without one, syncs
        relations across all accessible namespaces — the startup/rebuild path,
        where relations must be backfilled for every namespace or a rebuilt
        reporting database silently loses them.

        Returns:
            Dict with sync results (synced, failed, total)
        """
        # Rows route to their own namespace's schema (CASE-628). An explicit
        # namespace ensures its table eagerly (so the table exists for SQL
        # readers even when there are zero relations); the namespace-less
        # sweep ensures tables lazily per namespace seen.
        ns_tables: dict[str, str] = {}
        if namespace:
            ns_tables[namespace] = await self.schema_manager.ensure_term_relations_table(
                namespace
            )
        synced = 0
        failed = 0
        # False when a listing failed: a sync that never listed cannot be
        # told apart from an empty namespace by its counts alone.
        complete = True

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                page = 1

                while True:
                    params: dict = {"page": page, "page_size": page_size}
                    if namespace:
                        params["namespace"] = namespace
                    resp = await client.get(
                        f"{settings.def_store_url}/api/def-store/ontology/term-relations/all",
                        params=params,
                        headers={"X-API-Key": settings.api_key},
                    )
                    if resp.status_code != 200:
                        logger.error(f"Failed to list term_relations: {resp.status_code}")
                        complete = False
                        break

                    data = resp.json()
                    items = data.get("items", [])

                    if not items:
                        break

                    for rel in items:
                        ns = rel.get("namespace") or namespace or "wip"
                        if ns not in ns_tables:
                            ns_tables[ns] = await self.schema_manager.ensure_term_relations_table(ns)

                    async with self.pool.acquire() as conn:
                        for rel in items:
                            ns = rel.get("namespace") or namespace or "wip"
                            table_name = ns_tables[ns]
                            try:
                                await conn.execute(
                                    f"""
                                    INSERT INTO {table_name} AS cur (
                                        "namespace", "source_term_id", "target_term_id",
                                        "relation_type", "source_term_value", "target_term_value",
                                        "source_terminology_id", "target_terminology_id",
                                        "metadata", "status", "created_at", "created_by"
                                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                                    ON CONFLICT ("namespace", "source_term_id", "target_term_id", "relation_type")
                                    DO UPDATE SET
                                        "status" = EXCLUDED."status",
                                        "source_term_value" = EXCLUDED."source_term_value",
                                        "target_term_value" = EXCLUDED."target_term_value",
                                        "metadata" = EXCLUDED."metadata"
                                    {_only_if_changed(_TERM_RELATION_UPDATE_COLUMNS)}
                                    """,  # nosec B608  # identifiers validated by _safe_ident / ensure_*, values bound
                                    ns,
                                    rel["source_term_id"],
                                    rel["target_term_id"],
                                    rel["relation_type"],
                                    rel.get("source_term_value"),
                                    rel.get("target_term_value"),
                                    rel.get("source_terminology_id"),
                                    rel.get("target_terminology_id"),
                                    json.dumps(rel.get("metadata", {})),
                                    rel.get("status", "active"),
                                    _parse_datetime(rel.get("created_at")),
                                    rel.get("created_by"),
                                )
                                synced += 1
                            except Exception as e:
                                logger.error(f"Error syncing term_relation: {e}")
                                failed += 1

                    if page >= data.get("pages", 1):
                        break
                    page += 1
                    await asyncio.sleep(0.05)  # Small delay between pages

        except Exception as e:
            logger.error(f"Batch term_relation sync error: {e}", exc_info=True)
            complete = False

        # A batch is one burst by definition — rebuild each touched
        # namespace's closure once, immediately. A batch that wrote
        # nothing changed no edges, so there is nothing to rebuild.
        if synced:
            for ns in ns_tables:
                try:
                    await self.closure.rebuild(ns)
                except Exception as e:
                    logger.error(f"term_closure rebuild failed for {ns}: {e}")

        logger.info(f"TermRelation batch sync: {synced} synced, {failed} failed")
        return {"synced": synced, "failed": failed, "total": synced + failed, "complete": complete}

    def _table_lock(self, template_value: str) -> asyncio.Lock:
        """The lock every batch job writing ``template_value``'s tables holds."""
        return self._table_locks.setdefault(template_value.lower(), asyncio.Lock())

    def _forget_old_finished_jobs(self) -> None:
        """Keep at most MAX_FINISHED_JOBS finished jobs (the most recently
        finished): job records are in memory, and a service that triggers
        syncs routinely would otherwise grow them until an operator clears
        them. Pending and running jobs are never dropped."""
        finished = sorted(
            (
                job for job in self._jobs.values()
                if job.status in _FINISHED_STATUSES
            ),
            key=lambda job: job.completed_at or job.started_at or datetime.min.replace(tzinfo=UTC),
        )
        for job in finished[: max(0, len(finished) - MAX_FINISHED_JOBS)]:
            del self._jobs[job.job_id]
            self._running_tasks.pop(job.job_id, None)

    def clear_completed_jobs(self) -> int:
        """Clear completed/failed/cancelled jobs from memory."""
        to_remove = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in (
                BatchSyncStatus.COMPLETED,
                BatchSyncStatus.FAILED,
                BatchSyncStatus.CANCELLED,
            )
        ]
        for job_id in to_remove:
            del self._jobs[job_id]
            self._running_tasks.pop(job_id, None)
        return len(to_remove)
