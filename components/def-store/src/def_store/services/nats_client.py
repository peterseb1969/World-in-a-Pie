"""
NATS client for publishing def-store events.

Publishes events to NATS JetStream for consumption by the Reporting Sync service.
Events are published after successful terminology, term and term-relation
operations.

The connection, the WIP_EVENTS stream definition and the retry queue live in
the shared ``wip_auth.events.EventBus``: it connects in the background, heals
itself after any outage, and queues events while NATS is unreachable. This
module only shapes the def-store events.
"""

import json
import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from wip_auth.events import WIP_EVENTS_STREAM, EventBus, PublishOutcome

logger = logging.getLogger(__name__)

# Shared event bus - initialized on startup
_bus: EventBus = EventBus(None, name="def-store")


class EventType(StrEnum):
    """Def-store event types."""
    TERMINOLOGY_CREATED = "terminology.created"
    TERMINOLOGY_UPDATED = "terminology.updated"
    TERMINOLOGY_DELETED = "terminology.deleted"
    TERMINOLOGY_RESTORED = "terminology.restored"
    TERM_CREATED = "term.created"
    TERM_UPDATED = "term.updated"
    TERM_DEPRECATED = "term.deprecated"
    TERM_DELETED = "term.deleted"
    TERM_RELATION_CREATED = "term_relation.created"
    TERM_RELATION_DELETED = "term_relation.deleted"


async def configure_nats_client(nats_url: str) -> bool:
    """
    Start the event bus. Connecting happens in the background, so this never
    waits for NATS.

    Returns True if NATS is configured, False if event publishing is disabled.
    """
    global _bus

    _bus = EventBus(nats_url or None, name="def-store", streams=[WIP_EVENTS_STREAM])
    await _bus.start()
    return _bus.configured


async def close_nats_client():
    """Stop the event bus."""
    await _bus.stop()


def nats_stats() -> dict[str, Any]:
    """Connection state and publish/queue/drop counters for /health."""
    return _bus.stats()


async def _publish(subject: str, event_type: EventType, key: str, data: dict[str, Any],
                   changed_by: str | None) -> PublishOutcome:
    event = {
        "event_type": event_type.value,
        "timestamp": datetime.now(UTC).isoformat(),
        "changed_by": changed_by,
        key: data,
    }
    return await _bus.publish(subject, json.dumps(event).encode())


def _action(event_type: EventType) -> str:
    return event_type.value.split(".")[1]


async def publish_term_relation_event(
    event_type: EventType,
    term_relation: dict[str, Any],
    changed_by: str | None = None,
) -> PublishOutcome:
    """
    Publish a term-relation event to NATS.

    Args:
        event_type: Type of event (created, deleted)
        term_relation: Full term-relation data
        changed_by: User/system that made the change

    Returns:
        PUBLISHED, QUEUED (NATS unreachable; retried in the background) or
        DISABLED (NATS not configured)
    """
    # Subject: wip.term_relations.<source_terminology_id>.<action>
    terminology_id = term_relation.get("source_terminology_id", "unknown")
    subject = f"wip.term_relations.{terminology_id}.{_action(event_type)}"
    return await _publish(subject, event_type, "term_relation", term_relation, changed_by)


async def publish_terminology_event(
    event_type: EventType,
    terminology: dict[str, Any],
    changed_by: str | None = None,
) -> PublishOutcome:
    """
    Publish a terminology event to NATS.

    Args:
        event_type: Type of event (created, updated, deleted, restored)
        terminology: Full terminology data
        changed_by: User/system that made the change

    Returns:
        PUBLISHED, QUEUED or DISABLED, as for publish_term_relation_event
    """
    # Subject: wip.terminologies.<namespace>.<action>
    namespace = terminology["namespace"]
    subject = f"wip.terminologies.{namespace}.{_action(event_type)}"
    return await _publish(subject, event_type, "terminology", terminology, changed_by)


async def publish_term_event(
    event_type: EventType,
    term: dict[str, Any],
    changed_by: str | None = None,
) -> PublishOutcome:
    """
    Publish a term event to NATS.

    Args:
        event_type: Type of event (created, updated, deprecated, deleted)
        term: Full term data
        changed_by: User/system that made the change

    Returns:
        PUBLISHED, QUEUED or DISABLED, as for publish_term_relation_event
    """
    # Subject: wip.terms.<terminology_id>.<action>
    terminology_id = term.get("terminology_id", "unknown")
    subject = f"wip.terms.{terminology_id}.{_action(event_type)}"
    return await _publish(subject, event_type, "term", term, changed_by)


async def publish_term_events_bulk(
    event_type: EventType,
    terms: list[dict[str, Any]],
    changed_by: str | None = None,
) -> int:
    """
    Publish multiple term events to NATS.

    Used for bulk operations (create_terms_bulk).

    Returns:
        Number of events accepted for delivery (published or queued for retry)
    """
    accepted = 0
    for term in terms:
        outcome = await publish_term_event(event_type, term, changed_by)
        if outcome is not PublishOutcome.DISABLED:
            accepted += 1
    return accepted


async def health_check() -> bool:
    """Check if NATS connection is healthy."""
    return _bus.connected
