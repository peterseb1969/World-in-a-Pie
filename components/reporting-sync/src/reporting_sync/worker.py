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
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx
from nats.aio.client import Client as NATS
from nats.js import JetStreamContext
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

from .config import settings
from .metrics import metrics
from .models import EventType, ReportingConfig, SyncStatus
from .schema_manager import SchemaManager
from .transformer import DocumentTransformer, _parse_datetime

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
        Process a document event (created, updated, deleted, archived).

        Returns True if successful, False otherwise.
        """
        start_time = time.perf_counter()
        event_type = event_data.get("event_type")
        document = event_data.get("document", {})
        document_id = document.get("document_id")
        template_id = document.get("template_id")

        if not document_id or not template_id:
            logger.warning("Invalid document event: missing document_id or template_id")
            metrics.record_event_failed(None, None, "invalid_event", "Missing document_id or template_id")
            return False

        # Fetch template to get reporting config
        template = await self._fetch_template(template_id)
        if not template:
            logger.warning(f"Template {template_id} not found, skipping document {document_id}")
            metrics.record_event_failed(None, None, "template_not_found", f"Template {template_id} not found")
            return False

        template_value = template.get("value", "unknown")
        config = self._get_reporting_config(template)

        # Check if sync is enabled
        if not config.sync_enabled:
            logger.debug(f"Sync disabled for template {template_value}, skipping")
            metrics.record_event_skipped(template_value, "sync_disabled")
            return True  # Not an error, just skipped

        # Ensure table exists
        table_name = await self.schema_manager.ensure_table_for_template(template)
        if not table_name:
            metrics.record_event_skipped(template_value, "table_creation_skipped")
            return True  # Sync disabled

        # Transform document to rows (pass template for semantic type processing)
        transformer = DocumentTransformer(config)
        rows = transformer.transform(document, template)

        # Determine sync strategy
        strategy = config.sync_strategy.value

        # Handle delete/archive events — both set the document as inactive in PG
        if event_type in (EventType.DOCUMENT_DELETED.value, EventType.DOCUMENT_ARCHIVED.value):
            if event_type == EventType.DOCUMENT_DELETED.value and document.get("hard_delete"):
                # Hard-delete: remove rows from PostgreSQL
                async with self.pool.acquire() as conn:
                    target_version = document.get("version")
                    if target_version is not None:
                        await conn.execute(
                            f'DELETE FROM "{table_name}" WHERE document_id = $1 AND version = $2',
                            document_id, target_version,
                        )
                    else:
                        await conn.execute(
                            f'DELETE FROM "{table_name}" WHERE document_id = $1',
                            document_id,
                        )
                latency_ms = (time.perf_counter() - start_time) * 1000
                metrics.record_event_processed(template_value, table_name, latency_ms)
                logger.info(f"Hard-deleted document {document_id} from {table_name}")
                return True

            new_status = "archived" if event_type == EventType.DOCUMENT_ARCHIVED.value else "deleted"
            async with self.pool.acquire() as conn:
                await conn.execute(
                    f'UPDATE "{table_name}" SET status = $1 WHERE document_id = $2',
                    new_status,
                    document_id,
                )
            latency_ms = (time.perf_counter() - start_time) * 1000
            metrics.record_event_processed(template_value, table_name, latency_ms)
            logger.info(f"Marked document {document_id} as {new_status} in {table_name}")
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
                        # Truncate values to avoid logging sensitive document content (L4)
                        truncated = [
                            (str(v)[:100] + "..." if len(str(v)) > 100 else v)
                            for v in values
                        ]
                        logger.debug(f"Values (truncated): {truncated}")
                        raise

            latency_ms = (time.perf_counter() - start_time) * 1000
            metrics.record_event_processed(template_value, table_name, latency_ms)
            logger.info(
                f"Synced document {document_id} to {table_name} ({len(rows)} rows, {latency_ms:.1f}ms)"
            )
            return True

        except Exception as e:
            metrics.record_event_failed(template_value, table_name, "insert_error", str(e))
            raise

    async def _process_template_event(self, event_data: dict[str, Any]) -> bool:
        """
        Process a template event (created, updated, activated, deleted/deactivated).

        Creates or updates the PostgreSQL table schema AND syncs template
        metadata to the templates table (status tracking).
        """
        start_time = time.perf_counter()
        event_type = event_data.get("event_type")
        template = event_data.get("template", {})
        template_value = template.get("value")
        template_id = template.get("template_id")

        if not template_value:
            logger.warning("Invalid template event: missing value")
            return False

        # Clear template cache
        for key in list(self._template_cache.keys()):
            if self._template_cache[key].get("value") == template_value:
                del self._template_cache[key]

        config = self._get_reporting_config(template)

        if not config.sync_enabled:
            logger.info(f"Sync disabled for template {template_value}, skipping schema update")
            # Still sync template metadata even if doc sync is disabled
        else:
            # Create or update document table
            table_name = await self.schema_manager.ensure_table_for_template(template)
            logger.info(f"Ensured table {table_name} for template {template_value}")

        # Sync template metadata to templates table
        if template_id:
            namespace = template["namespace"]
            meta_table = await self.schema_manager.ensure_templates_table()

            try:
                async with self.pool.acquire() as conn:
                    if event_type == "template.deleted":
                        if template.get("hard_delete"):
                            # Hard-delete: remove from templates table
                            target_version = template.get("version")
                            if target_version is not None:
                                await conn.execute(
                                    f'DELETE FROM "{meta_table}" WHERE "namespace" = $1 AND "template_id" = $2 AND "version" = $3',
                                    namespace, template_id, target_version,
                                )
                            else:
                                await conn.execute(
                                    f'DELETE FROM "{meta_table}" WHERE "namespace" = $1 AND "template_id" = $2',
                                    namespace, template_id,
                                )
                        else:
                            await conn.execute(
                                f"""
                                UPDATE "{meta_table}"
                                SET "status" = 'inactive',
                                    "updated_at" = NOW(),
                                    "updated_by" = $3
                                WHERE "namespace" = $1
                                  AND "template_id" = $2
                                """,
                                namespace, template_id, event_data.get("changed_by"),
                            )
                    else:
                        await conn.execute(
                            f"""
                            INSERT INTO "{meta_table}" (
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
                            template_id,
                            namespace,
                            template_value,
                            template.get("label"),
                            template.get("description"),
                            template.get("version", 1),
                            template.get("status", "active"),
                            template.get("extends"),
                            template.get("extends_version"),
                            _parse_datetime(template.get("created_at")),
                            template.get("created_by"),
                            _parse_datetime(template.get("updated_at")),
                            template.get("updated_by"),
                        )

                latency_ms = (time.perf_counter() - start_time) * 1000
                logger.info(
                    f"Synced template {template_value} ({template_id}) "
                    f"to {meta_table} ({latency_ms:.1f}ms)"
                )
            except Exception as e:
                logger.error(f"Error syncing template metadata: {e}")
                raise

        return True

    async def _process_terminology_event(self, event_data: dict[str, Any]) -> bool:
        """
        Process a terminology event (created, updated, deleted, restored).

        Syncs the terminology to the terminologies table in PostgreSQL.
        """
        start_time = time.perf_counter()
        event_type = event_data.get("event_type")
        term_data = event_data.get("terminology", {})

        namespace = term_data["namespace"]
        terminology_id = term_data.get("terminology_id")

        if not terminology_id:
            logger.warning("Invalid terminology event: missing terminology_id")
            return False

        table_name = await self.schema_manager.ensure_terminologies_table()

        try:
            async with self.pool.acquire() as conn:
                if event_type == "terminology.deleted":
                    if term_data.get("hard_delete"):
                        # Hard delete: remove from terminologies table
                        await conn.execute(
                            f'DELETE FROM "{table_name}" WHERE "namespace" = $1 AND "terminology_id" = $2',
                            namespace, terminology_id,
                        )
                    else:
                        # Soft delete (existing behavior)
                        await conn.execute(
                            f"""
                            UPDATE "{table_name}"
                            SET "status" = 'inactive',
                                "updated_at" = NOW(),
                                "updated_by" = $3
                            WHERE "namespace" = $1
                              AND "terminology_id" = $2
                            """,
                            namespace, terminology_id, event_data.get("changed_by"),
                        )
                else:
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
                        terminology_id,
                        namespace,
                        term_data.get("value"),
                        term_data.get("label"),
                        term_data.get("description"),
                        term_data.get("case_sensitive", False),
                        term_data.get("allow_multiple", False),
                        term_data.get("extensible", True),
                        term_data.get("mutable", False),
                        term_data.get("status", "active"),
                        term_data.get("term_count", 0),
                        _parse_datetime(term_data.get("created_at")),
                        term_data.get("created_by"),
                        _parse_datetime(term_data.get("updated_at")),
                        term_data.get("updated_by"),
                    )

            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.info(
                f"Synced terminology {term_data.get('value')} ({terminology_id}) "
                f"to {table_name} ({latency_ms:.1f}ms)"
            )
            return True

        except Exception as e:
            logger.error(f"Error syncing terminology: {e}")
            raise

    async def _process_term_event(self, event_data: dict[str, Any]) -> bool:
        """
        Process a term event (created, updated, deprecated, deleted).

        Syncs the term to the terms table in PostgreSQL.
        """
        start_time = time.perf_counter()
        event_type = event_data.get("event_type")
        term_data = event_data.get("term", {})

        namespace = term_data["namespace"]
        term_id = term_data.get("term_id")

        if not term_id:
            logger.warning("Invalid term event: missing term_id")
            return False

        table_name = await self.schema_manager.ensure_terms_table()

        try:
            async with self.pool.acquire() as conn:
                if event_type == "term.deleted":
                    if term_data.get("hard_delete"):
                        # Hard delete: remove from terms and cascade relationships
                        rel_table = await self.schema_manager.ensure_term_relationships_table()
                        await conn.execute(
                            f'DELETE FROM "{rel_table}" WHERE "namespace" = $1 AND ("source_term_id" = $2 OR "target_term_id" = $2)',
                            namespace, term_id,
                        )
                        await conn.execute(
                            f'DELETE FROM "{table_name}" WHERE "namespace" = $1 AND "term_id" = $2',
                            namespace, term_id,
                        )
                    else:
                        # Soft delete (existing behavior)
                        await conn.execute(
                            f"""
                            UPDATE "{table_name}"
                            SET "status" = 'inactive',
                                "updated_at" = NOW(),
                                "updated_by" = $3
                            WHERE "namespace" = $1
                              AND "term_id" = $2
                            """,
                            namespace, term_id, event_data.get("changed_by"),
                        )
                elif event_type == "term.deprecated":
                    await conn.execute(
                        f"""
                        UPDATE "{table_name}"
                        SET "status" = 'deprecated',
                            "deprecated_reason" = $3,
                            "replaced_by_term_id" = $4,
                            "updated_at" = NOW(),
                            "updated_by" = $5
                        WHERE "namespace" = $1
                          AND "term_id" = $2
                        """,
                        namespace,
                        term_id,
                        term_data.get("deprecated_reason"),
                        term_data.get("replaced_by_term_id"),
                        event_data.get("changed_by"),
                    )
                else:
                    # Upsert for create/update
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
                        term_id,
                        namespace,
                        term_data.get("terminology_id"),
                        term_data.get("terminology_value"),
                        term_data.get("value"),
                        json.dumps(term_data.get("aliases", [])),
                        term_data.get("label"),
                        term_data.get("description"),
                        term_data.get("sort_order", 0),
                        term_data.get("parent_term_id"),
                        term_data.get("status", "active"),
                        term_data.get("deprecated_reason"),
                        term_data.get("replaced_by_term_id"),
                        _parse_datetime(term_data.get("created_at")),
                        term_data.get("created_by"),
                        _parse_datetime(term_data.get("updated_at")),
                        term_data.get("updated_by"),
                    )

            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.info(
                f"Synced term {term_data.get('value')} ({term_id}) "
                f"to {table_name} ({latency_ms:.1f}ms)"
            )
            return True

        except Exception as e:
            logger.error(f"Error syncing term: {e}")
            raise

    async def _process_relationship_event(self, event_data: dict[str, Any]) -> bool:
        """
        Process a relationship event (created, deleted).

        Syncs the relationship to the term_relationships table in PostgreSQL.
        """
        start_time = time.perf_counter()
        event_type = event_data.get("event_type")
        rel = event_data.get("relationship", {})

        namespace = rel["namespace"]
        source_term_id = rel.get("source_term_id")
        target_term_id = rel.get("target_term_id")
        relationship_type = rel.get("relationship_type")

        if not source_term_id or not target_term_id or not relationship_type:
            logger.warning("Invalid relationship event: missing required fields")
            return False

        # Ensure table exists
        table_name = await self.schema_manager.ensure_term_relationships_table()

        try:
            async with self.pool.acquire() as conn:
                if event_type == "relationship.deleted":
                    if rel.get("hard_delete"):
                        # Hard delete: remove from table
                        await conn.execute(
                            f"""
                            DELETE FROM "{table_name}"
                            WHERE "namespace" = $1
                              AND "source_term_id" = $2
                              AND "target_term_id" = $3
                              AND "relationship_type" = $4
                            """,
                            namespace, source_term_id, target_term_id, relationship_type,
                        )
                    else:
                        # Soft delete (existing behavior)
                        await conn.execute(
                            f"""
                            UPDATE "{table_name}"
                            SET "status" = 'inactive'
                            WHERE "namespace" = $1
                              AND "source_term_id" = $2
                              AND "target_term_id" = $3
                              AND "relationship_type" = $4
                            """,
                            namespace, source_term_id, target_term_id, relationship_type,
                        )
                else:
                    # Upsert for create/reactivate
                    await conn.execute(
                        f"""
                        INSERT INTO "{table_name}" (
                            "namespace", "source_term_id", "target_term_id",
                            "relationship_type", "source_term_value", "target_term_value",
                            "source_terminology_id", "target_terminology_id",
                            "metadata", "status", "created_at", "created_by"
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW(), $11)
                        ON CONFLICT ("namespace", "source_term_id", "target_term_id", "relationship_type")
                        DO UPDATE SET
                            "status" = EXCLUDED."status",
                            "source_term_value" = EXCLUDED."source_term_value",
                            "target_term_value" = EXCLUDED."target_term_value",
                            "metadata" = EXCLUDED."metadata",
                            "created_by" = EXCLUDED."created_by"
                        """,
                        namespace,
                        source_term_id,
                        target_term_id,
                        relationship_type,
                        rel.get("source_term_value"),
                        rel.get("target_term_value"),
                        rel.get("source_terminology_id"),
                        rel.get("target_terminology_id"),
                        json.dumps(rel.get("metadata", {})),
                        rel.get("status", "active"),
                        rel.get("created_by"),
                    )

            latency_ms = (time.perf_counter() - start_time) * 1000
            logger.info(
                f"Synced relationship {source_term_id} --{relationship_type}--> "
                f"{target_term_id} to {table_name} ({latency_ms:.1f}ms)"
            )
            return True

        except Exception as e:
            logger.error(f"Error syncing relationship: {e}")
            raise

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
            elif event_type.startswith("terminology."):
                success = await self._process_terminology_event(event_data)
            elif event_type.startswith("term."):
                success = await self._process_term_event(event_data)
            elif event_type.startswith("relationship."):
                success = await self._process_relationship_event(event_data)
            else:
                logger.warning(f"Unknown event type: {event_type}")
                success = True  # Don't retry unknown events

            if success:
                await msg.ack()
                self.status.events_processed += 1
                self.status.last_event_processed = datetime.now(UTC)
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
                except TimeoutError:
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
