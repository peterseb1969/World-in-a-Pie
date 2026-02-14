"""
NATS client for publishing template events.

Publishes events to NATS JetStream for consumption by the Reporting Sync service.
Events are published after successful template operations (create, update, delete).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)

# NATS client - initialized on startup
_nats_client = None
_jetstream = None
_nats_enabled = False


class EventType(str, Enum):
    """Template event types."""
    TEMPLATE_CREATED = "template.created"
    TEMPLATE_UPDATED = "template.updated"
    TEMPLATE_DELETED = "template.deleted"
    TEMPLATE_ACTIVATED = "template.activated"


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
        from nats.js.api import StreamConfig, RetentionPolicy

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
                subjects=["wip.documents.>", "wip.templates.>", "wip.files.>"],
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


async def close_nats_client():
    """Close the NATS connection."""
    global _nats_client, _jetstream, _nats_enabled

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


async def publish_template_event(
    event_type: EventType,
    template: dict[str, Any],
    changed_by: Optional[str] = None
) -> bool:
    """
    Publish a template event to NATS.

    Args:
        event_type: Type of event (created, updated, deleted)
        template: Full template data to include in event
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "changed_by": changed_by,
            "template": template,
        }

        # Determine subject based on event type
        # Format: wip.templates.<template_value>.<event_type>
        template_value = template.get("value", "unknown")
        subject = f"wip.templates.{template_value}.{event_type.value.split('.')[1]}"

        # Publish to JetStream
        payload = json.dumps(event).encode()
        ack = await _jetstream.publish(subject, payload)

        logger.debug(
            f"Published {event_type.value} event for template {template.get('template_id')} "
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
