"""
NATS client for publishing document events.

Publishes events to NATS JetStream for consumption by the Reporting Sync service.
Events are published after successful document operations (create, update, delete, archive).

Includes adaptive backpressure: when the reporting-sync consumer falls behind,
write endpoints add a small delay before returning, naturally slowing callers.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# NATS client - initialized on startup
_nats_client = None
_jetstream = None
_nats_enabled = False

# Backpressure — updated by background monitor, read by write endpoints
_throttle_delay: float = 0.0  # seconds
_backpressure_task: asyncio.Task | None = None


class EventType(str, Enum):
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
    Configure and connect the NATS client.

    Args:
        nats_url: NATS server URL (e.g., nats://localhost:4222)

    Returns:
        True if connected successfully, False otherwise
    """
    global _nats_client, _jetstream, _nats_enabled

    if not nats_url:
        logger.info("NATS URL not configured, event publishing disabled")
        _nats_enabled = False
        return False

    try:
        import nats
        from nats.js.api import RetentionPolicy, StreamConfig

        _nats_client = await nats.connect(nats_url)
        _jetstream = _nats_client.jetstream()

        # Ensure stream exists (same as reporting-sync creates)
        try:
            await _jetstream.stream_info("WIP_EVENTS")
            logger.info("Found existing WIP_EVENTS stream")
        except Exception:
            # Stream doesn't exist, create it
            stream_config = StreamConfig(
                name="WIP_EVENTS",
                # Use specific subjects to avoid overlap with WIP_INGEST stream
                subjects=["wip.documents.>", "wip.files.>"],
                retention=RetentionPolicy.LIMITS,
                max_msgs=1000000,
                max_bytes=1024 * 1024 * 1024,  # 1GB
                max_age=60 * 60 * 24 * 7,  # 7 days
            )
            await _jetstream.add_stream(stream_config)
            logger.info("Created WIP_EVENTS stream")

        _nats_enabled = True
        logger.info(f"NATS client connected to {nats_url}")
        return True

    except ImportError:
        logger.warning("nats-py not installed, event publishing disabled")
        _nats_enabled = False
        return False
    except Exception as e:
        logger.error(f"Failed to connect to NATS: {e}")
        _nats_enabled = False
        return False


def get_throttle_delay() -> float:
    """Return the current backpressure delay in seconds.

    Returns 0.0 when NATS is not configured or the pipeline is healthy.
    Write endpoints should ``await asyncio.sleep(get_throttle_delay())``
    after processing — when the delay is 0 this is a no-op.
    """
    return _throttle_delay


async def start_backpressure_monitor(
    nats_url: str,
    stream: str = "WIP_EVENTS",
    consumer: str = "reporting-sync-durable",
) -> None:
    """Start a background task that polls NATS consumer lag and adjusts throttle.

    Uses the JetStream API (already connected) to query the reporting-sync
    consumer's pending message count.  If the consumer doesn't exist yet
    (reporting-sync not deployed), the throttle stays at 0.
    """
    global _backpressure_task

    async def _monitor_loop():
        global _throttle_delay

        while True:
            try:
                if _jetstream:
                    info = await _jetstream.consumer_info(stream, consumer)
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

            except Exception:
                # Consumer doesn't exist or NATS unreachable — don't throttle
                _throttle_delay = 0.0

            await asyncio.sleep(5)

    _backpressure_task = asyncio.create_task(_monitor_loop())
    logger.info("Backpressure monitor started")


async def close_nats_client():
    """Close the NATS connection and stop the backpressure monitor."""
    global _nats_client, _jetstream, _nats_enabled, _backpressure_task, _throttle_delay

    if _backpressure_task:
        _backpressure_task.cancel()
        _backpressure_task = None
        _throttle_delay = 0.0

    if _nats_client:
        try:
            await _nats_client.close()
            logger.info("NATS client disconnected")
        except Exception as e:
            logger.error(f"Error closing NATS connection: {e}")

    _nats_client = None
    _jetstream = None
    _nats_enabled = False


def is_nats_enabled() -> bool:
    """Check if NATS publishing is enabled."""
    return _nats_enabled


async def publish_document_event(
    event_type: EventType,
    document: dict[str, Any],
    changed_by: str | None = None
) -> bool:
    """
    Publish a document event to NATS.

    Args:
        event_type: Type of event (created, updated, deleted, archived)
        document: Full document data to include in event
        changed_by: User/system that made the change

    Returns:
        True if published successfully, False otherwise
    """
    global _jetstream, _nats_enabled

    if not _nats_enabled or not _jetstream:
        logger.debug(f"NATS disabled, skipping event: {event_type.value}")
        return False

    try:
        # Build event payload
        event = {
            "event_type": event_type.value,
            "timestamp": datetime.now(UTC).isoformat(),
            "changed_by": changed_by,
            "document": document,
        }

        # Determine subject based on event type
        # Format: wip.documents.<template_id>.<event_type>
        template_id = document.get("template_id", "unknown")
        subject = f"wip.documents.{template_id}.{event_type.value.split('.')[1]}"

        # Publish to JetStream
        payload = json.dumps(event).encode()
        ack = await _jetstream.publish(subject, payload)

        logger.debug(
            f"Published {event_type.value} event for document {document.get('document_id')} "
            f"to {subject} (seq={ack.seq})"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to publish event {event_type.value}: {e}")
        return False


async def publish_file_event(
    event_type: EventType,
    file_data: dict[str, Any],
    changed_by: str | None = None
) -> bool:
    """
    Publish a file event to NATS.

    Args:
        event_type: Type of event (uploaded, updated, deleted)
        file_data: File metadata to include in event
        changed_by: User/system that made the change

    Returns:
        True if published successfully, False otherwise
    """
    global _jetstream, _nats_enabled

    if not _nats_enabled or not _jetstream:
        logger.debug(f"NATS disabled, skipping event: {event_type.value}")
        return False

    try:
        event = {
            "event_type": event_type.value,
            "timestamp": datetime.now(UTC).isoformat(),
            "changed_by": changed_by,
            "file": file_data,
        }

        # Format: wip.files.<file_id>.<action>
        file_id = file_data.get("file_id", "unknown")
        action = event_type.value.split(".")[1]
        subject = f"wip.files.{file_id}.{action}"

        payload = json.dumps(event).encode()
        ack = await _jetstream.publish(subject, payload)

        logger.debug(
            f"Published {event_type.value} event for file {file_id} "
            f"to {subject} (seq={ack.seq})"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to publish event {event_type.value}: {e}")
        return False


async def health_check() -> bool:
    """Check if NATS connection is healthy."""
    global _nats_client, _nats_enabled

    if not _nats_enabled or not _nats_client:
        return False

    try:
        return _nats_client.is_connected
    except Exception:
        return False
