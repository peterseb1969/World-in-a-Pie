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
import uuid
from datetime import UTC, datetime
from typing import Any

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
                    return response.json()
                logger.error(f"Failed to fetch template {template_id}: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching template {template_id}: {e}")
            return None

    async def _fetch_template_by_value(self, template_value: str) -> dict[str, Any] | None:
        """Fetch template by value from Template Store."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates/by-value/{template_value}",
                    headers={"X-API-Key": settings.api_key},
                    timeout=30.0,
                )
                if response.status_code == 200:
                    return response.json()
                logger.error(f"Failed to fetch template by code {template_value}: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching template by code {template_value}: {e}")
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
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Fetch documents from Document Store.

        Returns:
            Tuple of (documents, total_count)
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{settings.document_store_url}/api/document-store/documents",
                    params={
                        "template_id": template_id,
                        "status": status,
                        "page": page,
                        "page_size": page_size,
                    },
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

    async def start_batch_sync(
        self,
        template_value: str,
        force: bool = False,
        page_size: int = 100,
    ) -> BatchSyncJob:
        """
        Start a batch sync job for a template.

        Args:
            template_value: Template code to sync
            force: Force re-sync even if table has data
            page_size: Number of documents to fetch per page

        Returns:
            BatchSyncJob with job status
        """
        job_id = str(uuid.uuid4())[:8]
        job = BatchSyncJob(
            job_id=job_id,
            template_value=template_value,
            status=BatchSyncStatus.PENDING,
        )
        self._jobs[job_id] = job

        # Start async task
        task = asyncio.create_task(
            self._run_batch_sync(job, force, page_size)
        )
        self._running_tasks[job_id] = task

        return job

    async def _run_batch_sync(
        self,
        job: BatchSyncJob,
        force: bool,
        page_size: int,
    ) -> None:
        """Run the batch sync job."""
        job.status = BatchSyncStatus.RUNNING
        job.started_at = datetime.now(UTC)

        try:
            # Fetch template
            template = await self._fetch_template_by_value(job.template_value)
            if not template:
                job.status = BatchSyncStatus.FAILED
                job.error_message = f"Template {job.template_value} not found"
                job.completed_at = datetime.now(UTC)
                return

            # Resolve inherited fields from parent templates
            template = await self._resolve_template_fields(template)

            config = self._get_reporting_config(template)

            # Check if sync is enabled
            if not config.sync_enabled:
                job.status = BatchSyncStatus.COMPLETED
                job.error_message = "Sync disabled for this template"
                job.completed_at = datetime.now(UTC)
                return

            # Ensure table exists
            table_name = await self.schema_manager.ensure_table_for_template(template)
            if not table_name:
                job.status = BatchSyncStatus.FAILED
                job.error_message = "Failed to create table"
                job.completed_at = datetime.now(UTC)
                return

            # Check if table already has data (unless force)
            if not force:
                async with self.pool.acquire() as conn:
                    count = await conn.fetchval(f'SELECT COUNT(*) FROM "{table_name}"')
                    if count > 0:
                        job.status = BatchSyncStatus.COMPLETED
                        job.documents_synced = count
                        job.error_message = f"Table already has {count} rows. Use force=true to re-sync."
                        job.completed_at = datetime.now(UTC)
                        return

            template_id = template["template_id"]
            transformer = DocumentTransformer(config)
            strategy = config.sync_strategy.value

            # Fetch first page to get total count
            documents, total = await self._fetch_documents(template_id, 1, page_size)
            job.total_documents = total

            if total == 0:
                job.status = BatchSyncStatus.COMPLETED
                job.completed_at = datetime.now(UTC)
                logger.info(f"No documents to sync for {job.template_value}")
                return

            logger.info(f"Starting batch sync for {job.template_value}: {total} documents")

            # Process all pages
            page = 1
            while True:
                if page > 1:
                    documents, _ = await self._fetch_documents(template_id, page, page_size)

                if not documents:
                    break

                job.current_page = page

                # Process documents in this page
                async with self.pool.acquire() as conn:
                    for document in documents:
                        try:
                            rows = transformer.transform(document, template)
                            for row in rows:
                                sql, values = transformer.generate_upsert_sql(
                                    table_name, row, strategy
                                )
                                await conn.execute(sql, *values)
                            job.documents_synced += 1
                        except Exception as e:
                            logger.error(
                                f"Error syncing document {document.get('document_id')}: {e}"
                            )
                            job.documents_failed += 1

                # Check if we've processed all pages
                if len(documents) < page_size:
                    break
                page += 1

                # Small delay to avoid overwhelming the services
                await asyncio.sleep(0.1)

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

    async def start_batch_sync_all(
        self,
        force: bool = False,
        page_size: int = 100,
    ) -> list[BatchSyncJob]:
        """
        Start batch sync for all templates.

        Returns:
            List of BatchSyncJob for each template
        """
        # Sync terminologies and terms first (reference data)
        logger.info("Batch syncing terminologies...")
        term_results = await self.batch_sync_terminologies()
        logger.info(f"Terminologies: {term_results}")
        logger.info("Batch syncing terms...")
        terms_results = await self.batch_sync_terms()
        logger.info(f"Terms: {terms_results}")

        # Then sync documents per template
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

            job = await self.start_batch_sync(template_value, force, page_size)
            jobs.append(job)

            # Small delay between starting jobs
            await asyncio.sleep(0.5)

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
        table_name = await self.schema_manager.ensure_terminologies_table()
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

                    async with self.pool.acquire() as conn:
                        for t in items:
                            try:
                                await conn.execute(
                                    f"""
                                    INSERT INTO "{table_name}" (
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
        table_name = await self.schema_manager.ensure_templates_table()
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

                    async with self.pool.acquire() as conn:
                        for t in items:
                            try:
                                await conn.execute(
                                    f"""
                                    INSERT INTO "{table_name}" (
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
        table_name = await self.schema_manager.ensure_terms_table()
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

                        async with self.pool.acquire() as conn:
                            for t in items:
                                try:
                                    await conn.execute(
                                        f"""
                                        INSERT INTO "{table_name}" (
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

    async def batch_sync_relationships(
        self,
        namespace: str,
        page_size: int = 100,
    ) -> dict:
        """
        Batch sync all term relationships from Def-Store to PostgreSQL.

        Uses the /ontology/relationships/all endpoint for efficient pagination
        across all relationships (no per-term iteration needed).

        Returns:
            Dict with sync results (synced, failed, total)
        """
        table_name = await self.schema_manager.ensure_term_relationships_table()
        synced = 0
        failed = 0

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                page = 1

                while True:
                    resp = await client.get(
                        f"{settings.def_store_url}/api/def-store/ontology/relationships/all",
                        params={
                            "namespace": namespace,
                            "page": page,
                            "page_size": page_size,
                        },
                        headers={"X-API-Key": settings.api_key},
                    )
                    if resp.status_code != 200:
                        logger.error(f"Failed to list relationships: {resp.status_code}")
                        break

                    data = resp.json()
                    items = data.get("items", [])

                    if not items:
                        break

                    async with self.pool.acquire() as conn:
                        for rel in items:
                            try:
                                await conn.execute(
                                    f"""
                                    INSERT INTO "{table_name}" (
                                        "namespace", "source_term_id", "target_term_id",
                                        "relationship_type", "source_term_value", "target_term_value",
                                        "source_terminology_id", "target_terminology_id",
                                        "metadata", "status", "created_at", "created_by"
                                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                                    ON CONFLICT ("namespace", "source_term_id", "target_term_id", "relationship_type")
                                    DO UPDATE SET
                                        "status" = EXCLUDED."status",
                                        "source_term_value" = EXCLUDED."source_term_value",
                                        "target_term_value" = EXCLUDED."target_term_value",
                                        "metadata" = EXCLUDED."metadata"
                                    """,
                                    rel.get("namespace", namespace),
                                    rel["source_term_id"],
                                    rel["target_term_id"],
                                    rel["relationship_type"],
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
                                logger.error(f"Error syncing relationship: {e}")
                                failed += 1

                    if page >= data.get("pages", 1):
                        break
                    page += 1
                    await asyncio.sleep(0.05)  # Small delay between pages

        except Exception as e:
            logger.error(f"Batch relationship sync error: {e}", exc_info=True)

        logger.info(f"Relationship batch sync: {synced} synced, {failed} failed")
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
