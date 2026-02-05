"""
Batch Sync Service - Bulk sync documents from Document Store to PostgreSQL.

Responsibilities:
- Fetch documents from Document Store API (paginated)
- Transform and upsert into PostgreSQL
- Track progress and handle errors
- Support initial population and recovery scenarios
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
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
from .transformer import DocumentTransformer

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

    async def _fetch_template_by_code(self, template_code: str) -> dict[str, Any] | None:
        """Fetch template by code from Template Store."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates/by-code/{template_code}",
                    headers={"X-API-Key": settings.api_key},
                    timeout=30.0,
                )
                if response.status_code == 200:
                    return response.json()
                logger.error(f"Failed to fetch template by code {template_code}: {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching template by code {template_code}: {e}")
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
            f"Resolved {template['code']}: {len(child_fields)} own fields + "
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
        template_code: str,
        force: bool = False,
        page_size: int = 100,
    ) -> BatchSyncJob:
        """
        Start a batch sync job for a template.

        Args:
            template_code: Template code to sync
            force: Force re-sync even if table has data
            page_size: Number of documents to fetch per page

        Returns:
            BatchSyncJob with job status
        """
        job_id = str(uuid.uuid4())[:8]
        job = BatchSyncJob(
            job_id=job_id,
            template_code=template_code,
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
        job.started_at = datetime.now(timezone.utc)

        try:
            # Fetch template
            template = await self._fetch_template_by_code(job.template_code)
            if not template:
                job.status = BatchSyncStatus.FAILED
                job.error_message = f"Template {job.template_code} not found"
                job.completed_at = datetime.now(timezone.utc)
                return

            # Resolve inherited fields from parent templates
            template = await self._resolve_template_fields(template)

            config = self._get_reporting_config(template)

            # Check if sync is enabled
            if not config.sync_enabled:
                job.status = BatchSyncStatus.COMPLETED
                job.error_message = "Sync disabled for this template"
                job.completed_at = datetime.now(timezone.utc)
                return

            # Ensure table exists
            table_name = await self.schema_manager.ensure_table_for_template(template)
            if not table_name:
                job.status = BatchSyncStatus.FAILED
                job.error_message = "Failed to create table"
                job.completed_at = datetime.now(timezone.utc)
                return

            # Check if table already has data (unless force)
            if not force:
                async with self.pool.acquire() as conn:
                    count = await conn.fetchval(f'SELECT COUNT(*) FROM "{table_name}"')
                    if count > 0:
                        job.status = BatchSyncStatus.COMPLETED
                        job.documents_synced = count
                        job.error_message = f"Table already has {count} rows. Use force=true to re-sync."
                        job.completed_at = datetime.now(timezone.utc)
                        return

            template_id = template["template_id"]
            transformer = DocumentTransformer(config)
            strategy = config.sync_strategy.value

            # Fetch first page to get total count
            documents, total = await self._fetch_documents(template_id, 1, page_size)
            job.total_documents = total

            if total == 0:
                job.status = BatchSyncStatus.COMPLETED
                job.completed_at = datetime.now(timezone.utc)
                logger.info(f"No documents to sync for {job.template_code}")
                return

            logger.info(f"Starting batch sync for {job.template_code}: {total} documents")

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
            job.completed_at = datetime.now(timezone.utc)

            logger.info(
                f"Batch sync completed for {job.template_code}: "
                f"{job.documents_synced} synced, {job.documents_failed} failed"
            )

        except asyncio.CancelledError:
            job.status = BatchSyncStatus.CANCELLED
            job.completed_at = datetime.now(timezone.utc)
            logger.info(f"Batch sync cancelled for {job.template_code}")

        except Exception as e:
            job.status = BatchSyncStatus.FAILED
            job.error_message = str(e)
            job.completed_at = datetime.now(timezone.utc)
            logger.error(f"Batch sync failed for {job.template_code}: {e}", exc_info=True)

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
        templates = await self._list_templates()
        jobs = []

        for template in templates:
            template_code = template.get("code")
            if not template_code:
                continue

            # Check if sync is enabled
            config = self._get_reporting_config(template)
            if not config.sync_enabled:
                logger.info(f"Skipping {template_code}: sync disabled")
                continue

            job = await self.start_batch_sync(template_code, force, page_size)
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
