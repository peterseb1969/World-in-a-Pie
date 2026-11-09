"""Alert checks, pinned before check_alerts' refactor.

Provenance: CASE-1161. The suite never reached these branches: a lost
PostgreSQL connection while a connection alert is already active; for queue
lag, stalled processing and error rate, a critical level while the critical
alert is active, and a warning level while any alert of the type is active
(an active critical is never downgraded); and the webhook for new alerts.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from reporting_sync.metrics import MetricsCollector
from reporting_sync.models import AlertConfig, AlertSeverity, ConsumerInfo


def _consumer(pending: int) -> ConsumerInfo:
    return ConsumerInfo(stream_name="s", consumer_name="c", pending_messages=pending)


@pytest.mark.asyncio
async def test_active_alerts_are_not_raised_again_nor_downgraded_and_new_ones_are_sent():
    collector = MetricsCollector()
    collector.update_alert_config(AlertConfig(webhook_url="http://hook.invalid/x"))
    collector._send_webhook_notifications = AsyncMock()

    # Every condition at its critical level.
    collector.last_event_at = datetime.now(UTC) - timedelta(seconds=700)
    collector.events_processed, collector.events_failed = 1, 1
    first = await collector.check_alerts(_consumer(2000), nats_connected=True, postgres_connected=False)
    assert sorted((a.alert_type.value, a.severity.value) for a in first) == sorted([
        ("connection_lost", "critical"), ("queue_lag", "critical"),
        ("processing_stalled", "critical"), ("error_rate", "critical"),
    ])
    assert next(a for a in first if a.alert_type.value == "connection_lost").details == {"connection": "postgres"}
    collector._send_webhook_notifications.assert_awaited_once_with(first)

    # The same again: all already active, nothing new, nothing sent.
    again = await collector.check_alerts(_consumer(2000), nats_connected=True, postgres_connected=False)
    assert again == []
    assert collector._send_webhook_notifications.await_count == 1

    # Warning levels while the criticals are active: nothing new, still critical.
    collector.last_event_at = datetime.now(UTC) - timedelta(seconds=400)
    collector.events_processed, collector.events_failed = 9, 1
    warned = await collector.check_alerts(_consumer(500), nats_connected=True, postgres_connected=False)
    assert warned == []
    assert {a.alert_type.value: a.severity for a in collector.get_active_alerts()} == {
        "connection_lost": AlertSeverity.CRITICAL, "queue_lag": AlertSeverity.CRITICAL,
        "processing_stalled": AlertSeverity.CRITICAL, "error_rate": AlertSeverity.CRITICAL,
    }
