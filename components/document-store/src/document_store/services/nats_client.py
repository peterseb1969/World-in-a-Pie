"""
NATS client for publishing document events.

Publishes events to NATS JetStream for consumption by the Reporting Sync service.
Events are published after successful document operations (create, update, delete, archive).

The connection, the WIP_EVENTS stream definition and the retry queue live in
the shared ``wip_auth.events.EventBus``: it connects in the background, heals
itself after any outage, and queues events while NATS is unreachable. This
module only shapes the document and file events.

Includes adaptive backpressure: when the reporting-sync consumer falls behind,
write endpoints add a small delay before returning, naturally slowing callers.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from wip_auth.events import WIP_EVENTS_STREAM, EventBus, PublishOutcome

logger = logging.getLogger(__name__)

# Shared event bus - initialized on startup
_bus: EventBus = EventBus(None, name="document-store")

# Backpressure — updated by background monitor, read by write endpoints
_throttle_delay: float = 0.0  # seconds
_backpressure_task: asyncio.Task | None = None


class EventType(StrEnum):
    """Document and file event types."""
    DOCUMENT_CREATED = "document.created"
    DOCUMENT_UPDATED = "document.updated"
    DOCUMENT_DELETED = "document.deleted"
    DOCUMENT_ARCHIVED = "document.archived"
    FILE_UPLOADED = "file.uploaded"
    FILE_UPDATED = "file.updated"
    FILE_DELETED = "file.deleted"


async def configure_nats_client(nats_url: str) -> bool:
    """
    Start the event bus. Connecting happens in the background, so this never
    waits for NATS.

    Args:
        nats_url: NATS server URL (e.g., nats://localhost:4222)

    Returns:
        True if NATS is configured, False if event publishing is disabled
    """
    global _bus

    _bus = EventBus(nats_url or None, name="document-store", streams=[WIP_EVENTS_STREAM])
    await _bus.start()
    return _bus.configured


def get_jetstream() -> Any:
    """The live JetStream context, or None while NATS is not connected."""
    return _bus.jetstream


def nats_stats() -> dict[str, Any]:
    """Connection state and publish/queue/drop counters for /health."""
    return _bus.stats()


def get_throttle_delay() -> float:
    """Return the current backpressure delay in seconds.

    Returns 0.0 when NATS is not configured or the pipeline is healthy.
    Write endpoints should ``await asyncio.sleep(get_throttle_delay())``
    after processing — when the delay is 0 this is a no-op.
    """
    return _throttle_delay


async def start_backpressure_monitor(
    stream: str = "WIP_EVENTS",
    consumer: str = "reporting-sync-durable",
) -> None:
    """Start a background task that polls NATS consumer lag and adjusts throttle.

    Uses the event bus's JetStream context to query the reporting-sync
    consumer's pending message count.  If NATS is not connected or the
    consumer doesn't exist yet (reporting-sync not deployed), the throttle
    stays at 0.
    """
    global _backpressure_task

    async def _monitor_loop():
        global _throttle_delay

        while True:
            try:
                js = _bus.jetstream
                if js:
                    info = await js.consumer_info(stream, consumer)
                    pending = info.num_pending + info.num_ack_pending

                    prev = _throttle_delay
                    if pending > 50_000:
                        _throttle_delay = 0.5      # 500ms — heavy backlog
                    elif pending > 10_000:
                        _throttle_delay = 0.1      # 100ms — moderate backlog
                    elif pending > 1_000:
                        _throttle_delay = 0.05     # 50ms — mild backlog
                    else:
                        _throttle_delay = 0.0      # healthy

                    if _throttle_delay != prev:
                        if _throttle_delay > 0:
                            logger.info(
                                f"Backpressure: {pending} pending events, "
                                f"throttle {_throttle_delay*1000:.0f}ms"
                            )
                        else:
                            logger.info("Backpressure: pipeline caught up, throttle removed")
                else:
                    _throttle_delay = 0.0

            except Exception:
                # Consumer doesn't exist or NATS unreachable — don't throttle
                _throttle_delay = 0.0

            await asyncio.sleep(5)

    _backpressure_task = asyncio.create_task(_monitor_loop())
    logger.info("Backpressure monitor started")


async def close_nats_client():
    """Stop the backpressure monitor and the event bus."""
    global _backpressure_task, _throttle_delay

    if _backpressure_task:
        _backpressure_task.cancel()
        _backpressure_task = None
        _throttle_delay = 0.0

    await _bus.stop()


async def _publish(subject: str, event_type: EventType, key: str, data: dict[str, Any],
                   changed_by: str | None) -> PublishOutcome:
    event = {
        "event_type": event_type.value,
        "timestamp": datetime.now(UTC).isoformat(),
        "changed_by": changed_by,
        key: data,
    }
    return await _bus.publish(subject, json.dumps(event).encode())


async def publish_document_event(
    event_type: EventType,
    document: dict[str, Any],
    changed_by: str | None = None
) -> PublishOutcome:
    """
    Publish a document event to NATS.

    Args:
        event_type: Type of event (created, updated, deleted, archived)
        document: Full document data to include in event
        changed_by: User/system that made the change

    Returns:
        PUBLISHED, QUEUED (NATS unreachable; retried in the background) or
        DISABLED (NATS not configured)
    """
    # Format: wip.documents.<template_id>.<event_type>
    template_id = document.get("template_id", "unknown")
    subject = f"wip.documents.{template_id}.{event_type.value.split('.')[1]}"
    return await _publish(subject, event_type, "document", document, changed_by)


async def publish_file_event(
    event_type: EventType,
    file_data: dict[str, Any],
    changed_by: str | None = None
) -> PublishOutcome:
    """
    Publish a file event to NATS.

    Args:
        event_type: Type of event (uploaded, updated, deleted)
        file_data: File metadata to include in event
        changed_by: User/system that made the change

    Returns:
        PUBLISHED, QUEUED or DISABLED, as for publish_document_event
    """
    # Format: wip.files.<file_id>.<action>
    file_id = file_data.get("file_id", "unknown")
    action = event_type.value.split(".")[1]
    subject = f"wip.files.{file_id}.{action}"
    return await _publish(subject, event_type, "file", file_data, changed_by)


async def health_check() -> bool:
    """Check if NATS connection is healthy."""
    return _bus.connected
