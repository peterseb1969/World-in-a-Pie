"""The event models describe the envelope producers actually publish.

Provenance: CASE-1275 (04f8f4cb R4). Every producer sends
`{event_type, timestamp, changed_by, <entity>}` and the contract says an event
carries no id of its own (consumers deduplicate by the stream's message id),
while reporting-sync's own models required `event_id` and had no `changed_by` —
an event exactly as published would not have parsed against them.

"""

import json
from datetime import UTC, datetime

from reporting_sync.models import DocumentEvent, EventType, TemplateEvent


def _envelope(event_type: EventType, entity_key: str, entity: dict) -> str:
    """The envelope as services/nats_client._publish builds it."""
    return json.dumps({
        "event_type": event_type.value,
        "timestamp": datetime.now(UTC).isoformat(),
        "changed_by": "apikey:app",
        entity_key: entity,
    })


def test_a_published_document_event_parses():
    event = DocumentEvent.model_validate_json(
        _envelope(EventType.DOCUMENT_UPDATED, "document", {"document_id": "DOC-1"}))
    assert event.event_type is EventType.DOCUMENT_UPDATED
    assert event.changed_by == "apikey:app"
    assert event.document == {"document_id": "DOC-1"}


def test_a_published_template_event_parses():
    event = TemplateEvent.model_validate_json(
        _envelope(EventType.TEMPLATE_ACTIVATED, "template", {"template_id": "TPL-1"}))
    assert event.changed_by == "apikey:app"
    assert event.template == {"template_id": "TPL-1"}


def test_an_event_with_no_actor_parses():
    """`changed_by` is None when the publisher holds no actor identity."""
    payload = json.loads(_envelope(EventType.DOCUMENT_CREATED, "document", {}))
    payload["changed_by"] = None
    assert DocumentEvent.model_validate(payload).changed_by is None
