"""
Ingest Worker - Consumes messages from NATS WIP_INGEST stream.

Responsibilities:
- Subscribe to NATS JetStream for ingest messages
- Route messages to appropriate REST APIs via HTTP client
- Publish results to WIP_INGEST_RESULTS stream
- Handle retries and error logging
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

from nats.aio.client import Client as NATS
from nats.js import JetStreamContext
from nats.js.api import ConsumerConfig, DeliverPolicy, AckPolicy

from .config import settings
from .models import (
    IngestAction,
    IngestResult,
    IngestResultStatus,
    SUBJECT_TO_ACTION,
)
from .http_client import IngestHTTPClient
from .result_publisher import ResultPublisher

logger = logging.getLogger(__name__)


class IngestWorker:
    """Processes ingest messages from NATS and forwards to REST APIs."""

    def __init__(
        self,
        nats_client: NATS,
        jetstream: JetStreamContext,
        http_client: IngestHTTPClient,
        result_publisher: ResultPublisher,
    ):
        self.nc = nats_client
        self.js = jetstream
        self.http_client = http_client
        self.result_publisher = result_publisher
        self._running = False
        self._messages_processed = 0
        self._messages_failed = 0

    @property
    def messages_processed(self) -> int:
        """Total messages processed (success + failure)."""
        return self._messages_processed

    @property
    def messages_failed(self) -> int:
        """Messages that resulted in failed status."""
        return self._messages_failed

    @property
    def is_running(self) -> bool:
        """Whether the worker is currently running."""
        return self._running

    async def _process_message(self, msg: Any) -> None:
        """Process a single ingest message."""
        start_time = time.perf_counter()

        try:
            # Parse message data
            data = json.loads(msg.data.decode())
            subject = msg.subject

            # Determine action from subject
            action = SUBJECT_TO_ACTION.get(subject)
            if not action:
                logger.warning(f"Unknown ingest subject: {subject}")
                await msg.ack()  # Don't retry unknown subjects
                return

            # Extract correlation_id and payload
            # Support both wrapped format and direct payload
            if "correlation_id" in data and "payload" in data:
                correlation_id = data["correlation_id"]
                payload = data["payload"]
            else:
                # Direct payload - generate correlation_id
                correlation_id = data.get(
                    "correlation_id",
                    f"auto-{msg.metadata.sequence.stream}-{uuid.uuid4().hex[:8]}"
                )
                payload = data

            logger.debug(
                f"Processing {action.value} correlation_id={correlation_id}"
            )

            # Forward to appropriate REST API
            result = await self.http_client.forward_request(
                action, payload, correlation_id
            )

            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000
            result.duration_ms = duration_ms

            # Publish result to results stream
            await self.result_publisher.publish(result)

            # Update counters
            self._messages_processed += 1
            if result.status == IngestResultStatus.FAILED:
                self._messages_failed += 1

            # Acknowledge message (always ack after processing)
            await msg.ack()

            logger.info(
                f"Processed {action.value} correlation_id={correlation_id} "
                f"status={result.status.value} duration={duration_ms:.1f}ms"
            )

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in message: {e}")
            # Publish error result if we can extract any correlation info
            try:
                error_result = IngestResult(
                    correlation_id=f"parse-error-{uuid.uuid4().hex[:8]}",
                    action=IngestAction.DOCUMENTS_CREATE,  # Default
                    status=IngestResultStatus.FAILED,
                    error=f"Invalid JSON: {e}",
                    duration_ms=(time.perf_counter() - start_time) * 1000,
                )
                await self.result_publisher.publish(error_result)
            except Exception:
                pass
            self._messages_processed += 1
            self._messages_failed += 1
            await msg.ack()  # Don't retry invalid JSON

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            self._messages_processed += 1
            self._messages_failed += 1
            # NAK to retry with delay
            await msg.nak(delay=settings.retry_delay_ms / 1000)

    async def start(self) -> None:
        """Start the ingest worker."""
        logger.info("Starting ingest worker...")
        self._running = True

        try:
            # Create durable consumer configuration
            consumer_config = ConsumerConfig(
                durable_name=settings.nats_ingest_durable_name,
                deliver_policy=DeliverPolicy.ALL,
                ack_policy=AckPolicy.EXPLICIT,
                max_deliver=settings.retry_attempts,
                ack_wait=60,  # seconds - longer for HTTP calls
            )

            # Subscribe to all ingest subjects
            sub = await self.js.pull_subscribe(
                subject="wip.ingest.>",
                durable=settings.nats_ingest_durable_name,
                stream=settings.nats_ingest_stream_name,
                config=consumer_config,
            )

            logger.info(
                f"Subscribed to stream {settings.nats_ingest_stream_name} "
                f"with consumer {settings.nats_ingest_durable_name}"
            )

            # Main processing loop
            while self._running:
                try:
                    # Fetch batch of messages with timeout
                    messages = await sub.fetch(
                        batch=settings.batch_size,
                        timeout=5
                    )
                    for msg in messages:
                        await self._process_message(msg)

                except asyncio.TimeoutError:
                    # No messages available, continue loop
                    pass
                except asyncio.CancelledError:
                    logger.info("Worker cancelled, stopping...")
                    break
                except Exception as e:
                    logger.error(f"Error fetching messages: {e}")
                    await asyncio.sleep(1)  # Brief pause before retry

        except asyncio.CancelledError:
            logger.info("Worker task cancelled")
        except Exception as e:
            logger.error(f"Fatal error in ingest worker: {e}", exc_info=True)
            self._running = False
            raise
        finally:
            self._running = False
            logger.info("Ingest worker stopped")

    async def stop(self) -> None:
        """Stop the ingest worker."""
        logger.info("Stopping ingest worker...")
        self._running = False
