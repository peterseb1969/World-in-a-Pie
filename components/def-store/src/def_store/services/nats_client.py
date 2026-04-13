"""
NATS client for publishing def-store events.

Publishes events to NATS JetStream for consumption by the Reporting Sync service.
Events are published after successful ontology operations (relationship create/delete).
"""

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


class EventType(str, Enum):
    """Def-store event types."""
    TERMINOLOGY_CREATED = "terminology.created"
    TERMINOLOGY_UPDATED = "terminology.updated"
    TERMINOLOGY_DELETED = "terminology.deleted"
    TERMINOLOGY_RESTORED = "terminology.restored"
    TERM_CREATED = "term.created"
    TERM_UPDATED = "term.updated"
    TERM_DEPRECATED = "term.deprecated"
    TERM_DELETED = "term.deleted"
    RELATIONSHIP_CREATED = "relationship.created"
    RELATIONSHIP_DELETED = "relationship.deleted"


async def configure_nats_client(nats_url: str) -> bool:
    """
    Configure and connect the NATS client.

    Returns True if connected successfully, False otherwise.
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

        # All def-store subjects that must be in the stream
        required_subjects = [
            "wip.terminologies.>",
            "wip.terms.>",
            "wip.relationships.>",
        ]

        # Ensure stream exists with all def-store subjects
        try:
            info = await _jetstream.stream_info("WIP_EVENTS")
            current_subjects = list(info.config.subjects or [])
            missing = [s for s in required_subjects if s not in current_subjects]
            if missing:
                current_subjects.extend(missing)
                info.config.subjects = current_subjects
                await _jetstream.update_stream(info.config)
                logger.info(f"Added {missing} to WIP_EVENTS stream")
            else:
                logger.info("Found existing WIP_EVENTS stream with all def-store subjects")
        except Exception:
            # Stream doesn't exist, create it
            stream_config = StreamConfig(
                name="WIP_EVENTS",
                subjects=[
                    "wip.documents.>",
                    "wip.templates.>",
                    "wip.files.>",
                    "wip.terminologies.>",
                    "wip.terms.>",
                    "wip.relationships.>",
                ],
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


async def publish_relationship_event(
    event_type: EventType,
    relationship: dict[str, Any],
    changed_by: str | None = None,
) -> bool:
    """
    Publish a relationship event to NATS.

    Args:
        event_type: Type of event (created, deleted)
        relationship: Full relationship data
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
            "relationship": relationship,
        }

        # Subject: wip.relationships.<source_terminology_id>.<action>
        terminology_id = relationship.get("source_terminology_id", "unknown")
        action = event_type.value.split(".")[1]
        subject = f"wip.relationships.{terminology_id}.{action}"

        payload = json.dumps(event).encode()
        ack = await _jetstream.publish(subject, payload)

        logger.debug(
            f"Published {event_type.value} event: "
            f"{relationship.get('source_term_id')} --{relationship.get('relationship_type')}--> "
            f"{relationship.get('target_term_id')} (seq={ack.seq})"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to publish event {event_type.value}: {e}")
        return False


async def publish_terminology_event(
    event_type: EventType,
    terminology: dict[str, Any],
    changed_by: str | None = None,
) -> bool:
    """
    Publish a terminology event to NATS.

    Args:
        event_type: Type of event (created, updated, deleted, restored)
        terminology: Full terminology data
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
            "terminology": terminology,
        }

        # Subject: wip.terminologies.<namespace>.<action>
        namespace = terminology["namespace"]
        action = event_type.value.split(".")[1]
        subject = f"wip.terminologies.{namespace}.{action}"

        payload = json.dumps(event).encode()
        ack = await _jetstream.publish(subject, payload)

        logger.debug(
            f"Published {event_type.value} event: "
            f"{terminology.get('value')} (seq={ack.seq})"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to publish event {event_type.value}: {e}")
        return False


async def publish_term_event(
    event_type: EventType,
    term: dict[str, Any],
    changed_by: str | None = None,
) -> bool:
    """
    Publish a term event to NATS.

    Args:
        event_type: Type of event (created, updated, deprecated, deleted)
        term: Full term data
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
            "term": term,
        }

        # Subject: wip.terms.<terminology_id>.<action>
        terminology_id = term.get("terminology_id", "unknown")
        action = event_type.value.split(".")[1]
        subject = f"wip.terms.{terminology_id}.{action}"

        payload = json.dumps(event).encode()
        ack = await _jetstream.publish(subject, payload)

        logger.debug(
            f"Published {event_type.value} event: "
            f"{term.get('value')} in {terminology_id} (seq={ack.seq})"
        )
        return True

    except Exception as e:
        logger.error(f"Failed to publish event {event_type.value}: {e}")
        return False


async def publish_term_events_bulk(
    event_type: EventType,
    terms: list[dict[str, Any]],
    changed_by: str | None = None,
) -> int:
    """
    Publish multiple term events to NATS.

    Used for bulk operations (create_terms_bulk) to avoid per-term overhead.

    Returns:
        Number of successfully published events
    """
    global _jetstream, _nats_enabled

    if not _nats_enabled or not _jetstream:
        return 0

    published = 0
    for term in terms:
        try:
            event = {
                "event_type": event_type.value,
                "timestamp": datetime.now(UTC).isoformat(),
                "changed_by": changed_by,
                "term": term,
            }

            terminology_id = term.get("terminology_id", "unknown")
            action = event_type.value.split(".")[1]
            subject = f"wip.terms.{terminology_id}.{action}"

            payload = json.dumps(event).encode()
            await _jetstream.publish(subject, payload)
            published += 1
        except Exception as e:
            logger.error(f"Failed to publish bulk term event: {e}")

    if published:
        logger.debug(f"Published {published}/{len(terms)} {event_type.value} events")
    return published


async def health_check() -> bool:
    """Check if NATS connection is healthy."""
    global _nats_client, _nats_enabled

    if not _nats_enabled or not _nats_client:
        return False

    try:
        return _nats_client.is_connected
    except Exception:
        return False
