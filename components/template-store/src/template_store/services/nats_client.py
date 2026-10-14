"""
NATS client for publishing template events.

Publishes events to NATS JetStream for consumption by the Reporting Sync service.
Events are published after successful template operations (create, update, delete).

The connection, the WIP_EVENTS stream definition and the retry queue live in
the shared ``wip_auth.events.EventBus``: it connects in the background, heals
itself after any outage, and queues events while NATS is unreachable. This
module only shapes the template events.
"""

import json
import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from wip_auth.events import WIP_EVENTS_STREAM, EventBus, PublishOutcome

logger = logging.getLogger(__name__)

# Shared event bus - initialized on startup
_bus: EventBus = EventBus(None, name="template-store")


class EventType(StrEnum):
    """Template event types."""
    TEMPLATE_CREATED = "template.created"
    TEMPLATE_UPDATED = "template.updated"
    TEMPLATE_DELETED = "template.deleted"
    TEMPLATE_ACTIVATED = "template.activated"


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

    _bus = EventBus(nats_url or None, name="template-store", streams=[WIP_EVENTS_STREAM])
    await _bus.start()
    return _bus.configured


async def close_nats_client():
    """Stop the event bus."""
    await _bus.stop()


def nats_stats() -> dict[str, Any]:
    """Connection state and publish/queue/drop counters for /health."""
    return _bus.stats()


async def publish_template_event(
    event_type: EventType,
    template: dict[str, Any],
    changed_by: str | None = None
) -> PublishOutcome:
    """
    Publish a template event to NATS.

    Args:
        event_type: Type of event (created, updated, deleted)
        template: Full template data to include in event
        changed_by: User/system that made the change

    Returns:
        PUBLISHED, QUEUED (NATS unreachable; retried in the background) or
        DISABLED (NATS not configured)
    """
    event = {
        "event_type": event_type.value,
        "timestamp": datetime.now(UTC).isoformat(),
        "changed_by": changed_by,
        "template": template,
    }

    # Format: wip.templates.<template_value>.<event_type>
    template_value = template.get("value", "unknown")
    subject = f"wip.templates.{template_value}.{event_type.value.split('.')[1]}"
    return await _bus.publish(subject, json.dumps(event).encode())


async def health_check() -> bool:
    """Check if NATS connection is healthy."""
    return _bus.connected
