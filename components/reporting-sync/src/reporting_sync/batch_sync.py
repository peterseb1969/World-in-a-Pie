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
from .transformer import DocumentTransformer, _parse_datetime

logger = logging.getLogger(__name__)


class BatchSyncService:
    """Service for batch syncing documents to PostgreSQL."""

    def __init__(self, postgres_pool: asyncpg.Pool):
        self.pool = postgres_pool
        self.schema_manager = SchemaManager(postgres_pool)
        self._jobs: dict[str, BatchSyncJob] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
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
                # The value branch sorts version-descending: first = latest active.
                return cast(dict[str, Any] | None, items[0] if items else None)
        except Exception as e:
            logger.error(f"Error fetching active version of {template_value}: {e}")
            return None

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

    async def _resolve_template_fields(self, template: dict[str, Any]) -> dict[str, Any]:
        """
        Resolve template with all inherited fields from parent templates.

        The Template Store API returns templates with only their own fields.
        Documents using inheritance include fields from parent templates,
        so we need to recursively fetch parents and merge fields.
        """
        if not template.get("extends"):
            return template

        # Fetch parent template by ID
        parent_id = template["extends"]
        parent_template = await self._fetch_template(parent_id)

        if not parent_template:
            logger.warning(f"Could not fetch parent template {parent_id}, using template as-is")
            return template

        # Recursively resolve parent fields
        parent_template = await self._resolve_template_fields(parent_template)

        # Merge parent fields (parent fields come first, then child fields)
        parent_fields = parent_template.get("fields", [])
        child_fields = template.get("fields", [])

        # Child fields with same name override parent fields
        child_field_names = {f["name"] for f in child_fields}
        merged_fields = [f for f in parent_fields if f["name"] not in child_field_names]
        merged_fields.extend(child_fields)

        # Create resolved template
        resolved = template.copy()
        resolved["fields"] = merged_fields

        logger.debug(
            f"Resolved {template['value']}: {len(child_fields)} own fields + "
            f"{len(parent_fields)} parent fields = {len(merged_fields)} total"
        )

        return resolved

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
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Fetch documents from Document Store.

        `namespace` scopes to that namespace's documents. The filter is on
        the DOCUMENT, not the template: a document may be based on a
        template owned by another namespace, so resolving the template
        does not scope its documents.

        Returns:
            Tuple of (documents, total_count)
        """
        try:
            params: dict[str, Any] = {
                "template_id": template_id,
                "status": status,
                "page": page,
                "page_size": page_size,
            }
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
                    return data.get("items", []), data.get("total", 0)
                logger.error(f"Failed to fetch documents: {response.status_code}")
                return [], 0
        except Exception as e:
            logger.error(f"Error fetching documents: {e}")
            return [], 0

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
        """Run the batch sync job."""
        job.status = BatchSyncStatus.RUNNING
        job.started_at = datetime.now(UTC)

        try:
            # Resolve inherited fields from parent templates
            template = await self._resolve_template_fields(template)

            config = self._get_reporting_config(template)

            # Check if sync is enabled
            if not config.sync_enabled:
                job.status = BatchSyncStatus.COMPLETED
                job.error_message = "Sync disabled for this template"
                job.completed_at = datetime.now(UTC)
                return

            # Documents route to their own namespace's schema (CASE-628) AND
            # to their pinned template version's table (per-version split) —
            # a single template can fill several tables per namespace. Ensure
            # lazily per (namespace, version) as pages stream in. Each
            # document is transformed against the template version it
            # validated against (pin-to-what-validated), never against
            # latest — that mismatch was the NULL-conflation the split
            # eliminates.
            ns_tables: dict[tuple[str, int], str] = {}
            version_templates: dict[int, dict[str, Any]] = {}
            touched_namespaces: set[str] = set()

            latest_version = int(template.get("version", 1))
            version_templates[latest_version] = template

            async def _template_for_version(version: int) -> dict[str, Any]:
                if version in version_templates:
                    return version_templates[version]
                fetched = await self._fetch_template_version(
                    template["template_id"], version
                )
                if fetched is None:
                    # Pinned version unavailable — fall back to the latest
                    # definition, loudly: better a possibly-wider table than
                    # dropping the document.
                    logger.warning(
                        f"Template {job.template_value} v{version} not "
                        f"fetchable; falling back to v{latest_version}"
                    )
                    version_templates[version] = template
                    return template
                resolved = await self._resolve_template_fields(fetched)
                version_templates[version] = resolved
                return resolved

            # Eagerly ensure the latest version's table even when there are
            # zero documents: a freshly bootstrapped namespace (templates, no
            # docs yet) must be SQL-queryable — "no rows yet" is an empty
            # table (and view), not relation-does-not-exist. ONLY when the
            # template belongs to the job's scope, though: a scoped run
            # iterates the instance-wide template list, and eagerly ensuring
            # foreign templates would stamp every namespace's empty tables
            # into the target schema (this shipped once — ct-1000 grew 31
            # foreign kb/probe tables). Foreign-template tables are created
            # lazily below, only when scoped documents actually exist.
            tpl_ns = template.get("namespace") or "wip"

            # Force = drop-and-rebuild: remove the template's existing
            # relations in the target namespace before the sync recreates
            # them from current template shapes. Upserts cannot heal
            # mis-shaped DDL (a table created from a same-valued foreign
            # template rejects the namespace's own documents); only
            # drop-and-recreate can. Guarded to templates that belong to the
            # job's scope, mirroring the eager-ensure guard below: table
            # names derive from the template VALUE, so a foreign same-valued
            # template's job dropping here would race the owning template's
            # rebuild of the identically-named tables.
            if force and job.namespace is not None and job.namespace == tpl_ns:
                job.dropped_relations = (
                    await self.schema_manager.drop_relations_for_template(
                        tpl_ns, job.template_value, config
                    )
                )

            tpl_table = None
            if job.namespace is None or job.namespace == tpl_ns:
                # The eager ensure targets the latest ACTIVE version — the
                # version a new write lands on, and the version the restore
                # gate's structural parity demands. The template in hand may
                # be an INACTIVE newest version (the instance-wide listing
                # is deliberately status-free, so fully-deactivated
                # templates' documents still sync); ensuring ITS table while
                # parity waits for the active one's halted a restore at the
                # 30s gate. Documents pinned to inactive versions are
                # unaffected: their tables materialize lazily per page below.
                eager_template = template
                eager_version = latest_version
                # Absent status means active (the platform default) — only a
                # template explicitly deactivated triggers the re-fetch.
                if template.get("status", "active") != "active":
                    fetched_active = await self._fetch_latest_active_version(
                        job.template_value, tpl_ns
                    )
                    if fetched_active is None:
                        # No active version at all — parity's active-only
                        # listing skips this template too; nothing to ensure
                        # eagerly, the lazy path covers its documents.
                        eager_template = None
                    else:
                        eager_template = await self._resolve_template_fields(
                            fetched_active
                        )
                        eager_version = int(eager_template.get("version", 1))
                        version_templates[eager_version] = eager_template
                if eager_template is not None:
                    tpl_table = await self.schema_manager.ensure_table_for_template(
                        tpl_ns, eager_template
                    )
                    if tpl_table:
                        ns_tables[(tpl_ns, eager_version)] = tpl_table
                        touched_namespaces.add(tpl_ns)

            template_id = template["template_id"]
            strategy = config.sync_strategy.value

            # Physical version tables known per namespace: one catalog query
            # per namespace per job, kept current with tables this job
            # ensures. The per-document sibling cleanup below consults this
            # instead of re-asking information_schema for every document —
            # at restore scale that was one catalog query per document,
            # almost always to learn there are no siblings at all.
            ns_versions_on_disk: dict[str, set[int]] = {}

            async def _versions_on_disk(ns: str) -> set[int]:
                if ns not in ns_versions_on_disk:
                    ns_versions_on_disk[ns] = set(
                        (await self.schema_manager.list_version_tables(
                            ns, job.template_value, config
                        )).keys()
                    )
                return ns_versions_on_disk[ns]

            # Fetch first page to get total count
            fetch_started = time.monotonic()
            documents, total = await self._fetch_documents(
                template_id, 1, page_size, namespace=job.namespace
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
                logger.info(
                    f"No documents to sync for {job.template_value} {detail}"
                )
                return

            logger.info(f"Starting batch sync for {job.template_value}: {total} documents")

            # Process all pages
            page = 1
            while True:
                if page > 1:
                    fetch_started = time.monotonic()
                    documents, _ = await self._fetch_documents(
                        template_id, page, page_size, namespace=job.namespace
                    )
                    job.fetch_ms += int((time.monotonic() - fetch_started) * 1000)

                if not documents:
                    break

                job.current_page = page

                # Ensure a table for each (namespace, template_version)
                # present in this page. ensure_table_for_template uses its own
                # pooled connection, so do it before holding a connection for
                # the upserts.
                for document in documents:
                    ns = document.get("namespace") or "wip"
                    doc_tv = int(document.get("template_version", latest_version))
                    if (ns, doc_tv) not in ns_tables:
                        version_template = await _template_for_version(doc_tv)
                        ns_tables[(ns, doc_tv)] = (
                            await self.schema_manager.ensure_table_for_template(
                                ns, version_template
                            )
                        )
                        touched_namespaces.add(ns)
                        # Keep the sibling-table knowledge current with the
                        # table this job just ensured.
                        (await _versions_on_disk(ns)).add(doc_tv)

                # Process documents in this page, routing each to its
                # (schema, version table) and transforming against the
                # template version it validated against.
                upsert_started = time.monotonic()
                async with self.pool.acquire() as conn:
                    for document in documents:
                        try:
                            ns = document.get("namespace") or "wip"
                            doc_tv = int(document.get("template_version", latest_version))
                            table_name = ns_tables[(ns, doc_tv)]
                            version_template = await _template_for_version(doc_tv)
                            transformer = DocumentTransformer(config)
                            rows = transformer.transform(document, version_template)
                            for row in rows:
                                sql, values = transformer.generate_upsert_sql(
                                    table_name, row, strategy
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

                # Version-crossing upserts leave stale rows in sibling
                # version tables under latest_only — pair the page's upserts
                # with the sibling cleanup per document. Only when a sibling
                # table actually exists, though: the common case (single
                # version table, every restore) has nothing to clean, and
                # consulting the per-job knowledge instead of the catalog
                # keeps the check free per document. A sibling table created
                # by a concurrent writer mid-job is outside this knowledge —
                # that writer pairs its own upserts with its own cleanup, so
                # nothing is orphaned by skipping here.
                if strategy == "latest_only":
                    sibling_started = time.monotonic()
                    for document in documents:
                        ns = document.get("namespace") or "wip"
                        doc_tv = int(document.get("template_version", latest_version))
                        doc_id = document.get("document_id")
                        if doc_id and ((await _versions_on_disk(ns)) - {doc_tv}):
                            await self.schema_manager.delete_from_sibling_version_tables(
                                ns, job.template_value, config,
                                keep_version=doc_tv, document_id=doc_id,
                            )
                    job.sibling_ms += int((time.monotonic() - sibling_started) * 1000)

                # Check if we've processed all pages
                if len(documents) < page_size:
                    break
                page += 1

                # Yield to the event loop between pages. The flat 100 ms
                # sleep that used to sit here predates the per-template job
                # concurrency caps and cost ~2.6 min of pure sleep on a
                # 156k-document sync.
                await asyncio.sleep(0)

            # Rebuild the entity views for every namespace this job touched —
            # the union membership may have grown with lazily created version
            # tables. A legacy pre-split table is reported, never auto-dropped.
            for ns in touched_namespaces:
                warning = await self.schema_manager.ensure_views_for_template(
                    ns, job.template_value, config
                )
                if warning:
                    job.error_message = (
                        (job.error_message + "; " if job.error_message else "") + warning
                    )

            job.status = BatchSyncStatus.COMPLETED
            job.completed_at = datetime.now(UTC)

            logger.info(
                f"Batch sync completed for {job.template_value}: "
                f"{job.documents_synced} synced, {job.documents_failed} failed"
            )

        except asyncio.CancelledError:
            job.status = BatchSyncStatus.CANCELLED
            job.completed_at = datetime.now(UTC)
            logger.info(f"Batch sync cancelled for {job.template_value}")

        except Exception as e:
            job.status = BatchSyncStatus.FAILED
            job.error_message = str(e)
            job.completed_at = datetime.now(UTC)
            logger.error(f"Batch sync failed for {job.template_value}: {e}", exc_info=True)

    async def _sync_definitions(
        self, namespace: str | None, page_size: int
    ) -> None:
        """Terminology + term sync, run as a background companion of
        batch-all. Not a dependency of the document jobs: the document
        upsert path transforms the document JSON against its template and
        never reads the terminologies/terms tables."""
        logger.info("Batch syncing terminologies...")
        term_results = await self.batch_sync_terminologies(
            namespace=namespace, page_size=page_size
        )
        logger.info(f"Terminologies: {term_results}")
        logger.info("Batch syncing terms...")
        terms_results = await self.batch_sync_terms(
            namespace=namespace, page_size=page_size
        )
        logger.info(f"Terms: {terms_results}")

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
                                    INSERT INTO {table_name} (
                                        "terminology_id", "namespace", "value", "label",
                                        "description", "case_sensitive", "allow_multiple",
                                        "extensible", "mutable", "status", "term_count",
                                        "created_at", "created_by", "updated_at", "updated_by"
                                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                                    ON CONFLICT ("namespace", "terminology_id")
                                    DO UPDATE SET
                                        "value" = EXCLUDED."value",
                                        "label" = EXCLUDED."label",
                                        "description" = EXCLUDED."description",
                                        "case_sensitive" = EXCLUDED."case_sensitive",
                                        "allow_multiple" = EXCLUDED."allow_multiple",
                                        "extensible" = EXCLUDED."extensible",
                                        "mutable" = EXCLUDED."mutable",
                                        "status" = EXCLUDED."status",
                                        "term_count" = EXCLUDED."term_count",
                                        "updated_at" = EXCLUDED."updated_at",
                                        "updated_by" = EXCLUDED."updated_by"
                                    """,
                                    t["terminology_id"],
                                    t.get("namespace", namespace),
                                    t["value"],
                                    t.get("label"),
                                    t.get("description"),
                                    t.get("case_sensitive", False),
                                    t.get("allow_multiple", False),
                                    t.get("extensible", True),
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

        logger.info(f"Terminology batch sync: {synced} synced, {failed} failed")
        return {"synced": synced, "failed": failed, "total": synced + failed}

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
                                    """,
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

        logger.info(f"Template batch sync: {synced} synced, {failed} failed")
        return {"synced": synced, "failed": failed, "total": synced + failed}

    async def batch_sync_terms(
        self,
        namespace: str | None = None,
        page_size: int = 100,
    ) -> dict:
        """
        Batch sync all terms from Def-Store to PostgreSQL.

        Iterates through all terminologies and fetches their terms.

        Returns:
            Dict with sync results (synced, failed, total)
        """
        # Rows route to their own namespace's schema (CASE-628).
        ns_tables: dict[str, str] = {}
        synced = 0
        failed = 0

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # First, get all terminology IDs
                terminology_ids = []
                page = 1
                while True:
                    term_list_params: dict = {"page": page, "page_size": 100}
                    if namespace:
                        term_list_params["namespace"] = namespace
                    resp = await client.get(
                        f"{settings.def_store_url}/api/def-store/terminologies",
                        params=term_list_params,
                        headers={"X-API-Key": settings.api_key},
                    )
                    if resp.status_code != 200:
                        break
                    data = resp.json()
                    for t in data.get("items", []):
                        terminology_ids.append(t["terminology_id"])
                    if page >= data.get("pages", 1):
                        break
                    page += 1

                logger.info(f"Found {len(terminology_ids)} terminologies, fetching terms...")

                # Fetch terms per terminology
                for tidx, tid in enumerate(terminology_ids):
                    page = 1
                    while True:
                        resp = await client.get(
                            f"{settings.def_store_url}/api/def-store/terminologies/{tid}/terms",
                            params={
                                "page": page,
                                "page_size": page_size,
                            },
                            headers={"X-API-Key": settings.api_key},
                        )
                        if resp.status_code != 200:
                            logger.error(f"Failed to list terms for {tid}: {resp.status_code}")
                            break

                        data = resp.json()
                        items = data.get("items", [])

                        if not items:
                            break

                        # Ensure a table per namespace present in this page.
                        for t in items:
                            ns = t.get("namespace") or namespace or "wip"
                            if ns not in ns_tables:
                                ns_tables[ns] = await self.schema_manager.ensure_terms_table(ns)

                        async with self.pool.acquire() as conn:
                            for t in items:
                                try:
                                    table_name = ns_tables[t.get("namespace") or namespace or "wip"]
                                    await conn.execute(
                                        f"""
                                        INSERT INTO {table_name} (
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
                                        """,
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
                                    synced += 1
                                except Exception as e:
                                    logger.error(f"Error syncing term {t.get('value')}: {e}")
                                    failed += 1

                        if page >= data.get("pages", 1):
                            break
                        page += 1
                        await asyncio.sleep(0.05)

                    if (tidx + 1) % 10 == 0:
                        logger.info(
                            f"Term sync progress: {tidx+1}/{len(terminology_ids)} terminologies, "
                            f"{synced} terms synced"
                        )

        except Exception as e:
            logger.error(f"Batch term sync error: {e}", exc_info=True)

        logger.info(f"Term batch sync: {synced} synced, {failed} failed")
        return {"synced": synced, "failed": failed, "total": synced + failed}

    async def batch_sync_term_relations(
        self,
        namespace: str | None = None,
        page_size: int = 100,
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
                                    INSERT INTO {table_name} (
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
                                    """,
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

        logger.info(f"TermRelation batch sync: {synced} synced, {failed} failed")
        return {"synced": synced, "failed": failed, "total": synced + failed}

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
