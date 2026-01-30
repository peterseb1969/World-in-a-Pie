"""
Sync Worker - Consumes events from NATS and syncs to PostgreSQL.

Responsibilities:
- Subscribe to NATS JetStream for document and template events
- Process events asynchronously
- Handle retries and error logging
- Track sync status
- Record metrics for monitoring
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any

import asyncpg
import httpx
from nats.aio.client import Client as NATS
from nats.js import JetStreamContext
from nats.js.api import ConsumerConfig, DeliverPolicy, AckPolicy

from .config import settings
from .metrics import metrics
from .models import EventType, ReportingConfig, SyncStatus, SyncStrategy
from .schema_manager import SchemaManager
from .transformer import DocumentTransformer

logger = logging.getLogger(__name__)


class SyncWorker:
    """Processes events from NATS and syncs to PostgreSQL."""

    def __init__(
        self,
        nats_client: NATS,
        jetstream: JetStreamContext,
        postgres_pool: asyncpg.Pool,
        status: SyncStatus,
    ):
        self.nc = nats_client
        self.js = jetstream
        self.pool = postgres_pool
        self.status = status
        self.schema_manager = SchemaManager(postgres_pool)
        self._running = False
        self._template_cache: dict[str, dict[str, Any]] = {}

    async def _fetch_template(self, template_id: str) -> dict[str, Any] | None:
        """Fetch template definition from Template Store."""
        if template_id in self._template_cache:
            return self._template_cache[template_id]

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{settings.template_store_url}/api/template-store/templates/{template_id}",
                    headers={"X-API-Key": settings.api_key},
                    timeout=30.0,
                )
                if response.status_code == 200:
                    template = response.json()
                    self._template_cache[template_id] = template
                    return template
                else:
                    logger.error(
                        f"Failed to fetch template {template_id}: {response.status_code}"
                    )
                    return None
        except Exception as e:
            logger.error(f"Error fetching template {template_id}: {e}")
            return None

    def _get_reporting_config(self, template: dict[str, Any]) -> ReportingConfig:
        """Extract reporting config from template."""
        reporting_data = template.get("reporting", {})
        return ReportingConfig(**reporting_data) if reporting_data else ReportingConfig()

    async def _process_document_event(self, event_data: dict[str, Any]) -> bool:
        """
        Process a document event (created, updated, deleted).

        Returns True if successful, False otherwise.
        """
        start_time = time.perf_counter()
        event_type = event_data.get("event_type")
        document = event_data.get("document", {})
        document_id = document.get("document_id")
        template_id = document.get("template_id")

        if not document_id or not template_id:
            logger.warning(f"Invalid document event: missing document_id or template_id")
            metrics.record_event_failed(None, None, "invalid_event", "Missing document_id or template_id")
            return False

        # Fetch template to get reporting config
        template = await self._fetch_template(template_id)
        if not template:
            logger.warning(f"Template {template_id} not found, skipping document {document_id}")
            metrics.record_event_failed(None, None, "template_not_found", f"Template {template_id} not found")
            return False

        template_code = template.get("code", "unknown")
        config = self._get_reporting_config(template)

        # Check if sync is enabled
        if not config.sync_enabled:
            logger.debug(f"Sync disabled for template {template_code}, skipping")
            metrics.record_event_skipped(template_code, "sync_disabled")
            return True  # Not an error, just skipped

        # Ensure table exists
        table_name = await self.schema_manager.ensure_table_for_template(template)
        if not table_name:
            metrics.record_event_skipped(template_code, "table_creation_skipped")
            return True  # Sync disabled

        # Transform document to rows
        transformer = DocumentTransformer(config)
        rows = transformer.transform(document)

        # Determine sync strategy
        strategy = config.sync_strategy.value

        # Handle delete events
        if event_type == EventType.DOCUMENT_DELETED.value:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    f'UPDATE "{table_name}" SET status = $1 WHERE document_id = $2',
                    "deleted",
                    document_id,
                )
            latency_ms = (time.perf_counter() - start_time) * 1000
            metrics.record_event_processed(template_code, table_name, latency_ms)
            logger.info(f"Marked document {document_id} as deleted in {table_name}")
            return True

        # Insert/update rows
        try:
            async with self.pool.acquire() as conn:
                for row in rows:
                    sql, values = transformer.generate_upsert_sql(table_name, row, strategy)
                    try:
                        await conn.execute(sql, *values)
                    except Exception as e:
                        logger.error(f"Error inserting row for {document_id}: {e}")
                        logger.debug(f"SQL: {sql}")
                        logger.debug(f"Values: {values}")
                        raise

            latency_ms = (time.perf_counter() - start_time) * 1000
            metrics.record_event_processed(template_code, table_name, latency_ms)
            logger.info(
                f"Synced document {document_id} to {table_name} ({len(rows)} rows, {latency_ms:.1f}ms)"
            )
            return True

        except Exception as e:
            metrics.record_event_failed(template_code, table_name, "insert_error", str(e))
            raise

    async def _process_template_event(self, event_data: dict[str, Any]) -> bool:
        """
        Process a template event (created, updated).

        Creates or updates the PostgreSQL table schema.
        """
        template = event_data.get("template", {})
        template_code = template.get("code")

        if not template_code:
            logger.warning("Invalid template event: missing code")
            return False

        # Clear template cache
        for key in list(self._template_cache.keys()):
            if self._template_cache[key].get("code") == template_code:
                del self._template_cache[key]

        config = self._get_reporting_config(template)

        if not config.sync_enabled:
            logger.info(f"Sync disabled for template {template_code}, skipping schema update")
            return True

        # Create or update table
        table_name = await self.schema_manager.ensure_table_for_template(template)
        logger.info(f"Ensured table {table_name} for template {template_code}")

        return True

    async def _process_message(self, msg) -> None:
        """Process a single NATS message."""
        try:
            # Parse event data
            event_data = json.loads(msg.data.decode())
            event_type = event_data.get("event_type", "")

            logger.debug(f"Processing event: {event_type}")

            success = False

            if event_type.startswith("document."):
                success = await self._process_document_event(event_data)
            elif event_type.startswith("template."):
                success = await self._process_template_event(event_data)
            else:
                logger.warning(f"Unknown event type: {event_type}")
                success = True  # Don't retry unknown events

            if success:
                await msg.ack()
                self.status.events_processed += 1
                self.status.last_event_processed = datetime.utcnow()
            else:
                # Negative ack for retry
                await msg.nak(delay=settings.retry_delay_ms / 1000)
                self.status.events_failed += 1

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in message: {e}")
            await msg.ack()  # Don't retry invalid messages
            self.status.events_failed += 1

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            await msg.nak(delay=settings.retry_delay_ms / 1000)
            self.status.events_failed += 1

    async def start(self) -> None:
        """Start the sync worker."""
        logger.info("Starting sync worker...")
        self._running = True
        self.status.running = True

        # Create durable consumer
        try:
            consumer_config = ConsumerConfig(
                durable_name=settings.nats_durable_name,
                deliver_policy=DeliverPolicy.ALL,
                ack_policy=AckPolicy.EXPLICIT,
                max_deliver=settings.retry_attempts,
                ack_wait=30,  # seconds
            )

            # Subscribe to the stream
            sub = await self.js.pull_subscribe(
                subject="wip.>",
                durable=settings.nats_durable_name,
                stream=settings.nats_stream_name,
                config=consumer_config,
            )

            logger.info(
                f"Subscribed to stream {settings.nats_stream_name} "
                f"with consumer {settings.nats_durable_name}"
            )

            # Process messages
            while self._running:
                try:
                    messages = await sub.fetch(batch=settings.batch_size, timeout=5)
                    for msg in messages:
                        await self._process_message(msg)
                except asyncio.TimeoutError:
                    # No messages available, continue
                    pass
                except Exception as e:
                    logger.error(f"Error fetching messages: {e}")
                    await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Fatal error in sync worker: {e}", exc_info=True)
            self._running = False
            self.status.running = False
            raise

    async def stop(self) -> None:
        """Stop the sync worker."""
        logger.info("Stopping sync worker...")
        self._running = False
        self.status.running = False


async def run_sync_worker(
    nats_client: NATS,
    jetstream: JetStreamContext,
    postgres_pool: asyncpg.Pool,
    status: SyncStatus,
) -> None:
    """Run the sync worker."""
    worker = SyncWorker(nats_client, jetstream, postgres_pool, status)
    await worker.start()
