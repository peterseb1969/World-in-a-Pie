"""
Tests for the MetricsCollector.

Covers event recording, latency statistics, per-template aggregation,
error tracking, uptime calculation, and the full alert lifecycle.
"""

from datetime import UTC, datetime, timedelta

import pytest

from reporting_sync.metrics import MetricsCollector
from reporting_sync.models import (
    AlertConfig,
    AlertSeverity,
    AlertThresholds,
    AlertType,
    ConsumerInfo,
)

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def collector():
    """Fresh MetricsCollector for each test."""
    return MetricsCollector(max_latency_samples=100, max_alerts_history=50)


# =========================================================================
# record_event_processed
# =========================================================================


class TestRecordEventProcessed:
    """Tests for recording successfully processed events."""

    def test_increments_events_processed(self, collector: MetricsCollector):
        collector.record_event_processed("person", "doc_person", 5.0)
        assert collector.events_processed == 1

    def test_multiple_events_increment_correctly(self, collector: MetricsCollector):
        for i in range(5):
            collector.record_event_processed("person", "doc_person", float(i))
        assert collector.events_processed == 5

    def test_updates_last_event_at(self, collector: MetricsCollector):
        assert collector.last_event_at is None
        collector.record_event_processed("person", "doc_person", 10.0)
        assert collector.last_event_at is not None
        assert (datetime.now(UTC) - collector.last_event_at).total_seconds() < 2

    def test_appends_latency_sample(self, collector: MetricsCollector):
        collector.record_event_processed("person", "doc_person", 42.5)
        assert 42.5 in collector._latency_samples

    def test_creates_template_stats_entry(self, collector: MetricsCollector):
        collector.record_event_processed("person", "doc_person", 10.0)
        assert "person" in collector._template_stats
        stats = collector._template_stats["person"]
        assert stats.template_value == "person"
        assert stats.table_name == "doc_person"
        assert stats.documents_synced == 1

    def test_increments_existing_template_stats(self, collector: MetricsCollector):
        collector.record_event_processed("person", "doc_person", 5.0)
        collector.record_event_processed("person", "doc_person", 8.0)
        assert collector._template_stats["person"].documents_synced == 2

    def test_tracks_multiple_templates_independently(self, collector: MetricsCollector):
        collector.record_event_processed("person", "doc_person", 5.0)
        collector.record_event_processed("address", "doc_address", 3.0)
        assert collector._template_stats["person"].documents_synced == 1
        assert collector._template_stats["address"].documents_synced == 1


# =========================================================================
# record_event_failed
# =========================================================================


class TestRecordEventFailed:
    """Tests for recording failed events."""

    def test_increments_events_failed(self, collector: MetricsCollector):
        collector.record_event_failed("person", "doc_person", "insert_error", "duplicate key")
        assert collector.events_failed == 1

    def test_tracks_error_type(self, collector: MetricsCollector):
        collector.record_event_failed("person", "doc_person", "insert_error", "bad data")
        assert collector._errors_by_type["insert_error"] == 1

    def test_accumulates_same_error_type(self, collector: MetricsCollector):
        collector.record_event_failed("person", None, "insert_error", "err1")
        collector.record_event_failed("person", None, "insert_error", "err2")
        assert collector._errors_by_type["insert_error"] == 2

    def test_tracks_multiple_error_types(self, collector: MetricsCollector):
        collector.record_event_failed(None, None, "invalid_event", "missing field")
        collector.record_event_failed(None, None, "template_not_found", "0190c000-0000-7000-0000-000000000999")
        assert collector._errors_by_type["invalid_event"] == 1
        assert collector._errors_by_type["template_not_found"] == 1

    def test_updates_template_stats_on_failure(self, collector: MetricsCollector):
        collector.record_event_failed("person", "doc_person", "insert_error", "oops")
        stats = collector._template_stats["person"]
        assert stats.documents_failed == 1
        assert stats.last_error == "oops"
        assert stats.last_error_at is not None

    def test_skips_template_stats_when_template_value_is_none(self, collector: MetricsCollector):
        collector.record_event_failed(None, None, "invalid_event", "bad payload")
        assert len(collector._template_stats) == 0

    def test_updates_last_event_at(self, collector: MetricsCollector):
        collector.record_event_failed("person", "doc_person", "error", "msg")
        assert collector.last_event_at is not None


# =========================================================================
# record_event_skipped
# =========================================================================


class TestRecordEventSkipped:
    """Tests for recording skipped events."""

    def test_increments_events_processed(self, collector: MetricsCollector):
        """Skipped events still count as processed (not failures)."""
        collector.record_event_skipped("person", "sync_disabled")
        assert collector.events_processed == 1
        assert collector.events_failed == 0

    def test_updates_last_event_at(self, collector: MetricsCollector):
        collector.record_event_skipped("person", "sync_disabled")
        assert collector.last_event_at is not None

    def test_does_not_add_latency_sample(self, collector: MetricsCollector):
        collector.record_event_skipped("person", "sync_disabled")
        assert len(collector._latency_samples) == 0


# =========================================================================
# get_latency_stats
# =========================================================================


class TestGetLatencyStats:
    """Tests for latency statistics calculation."""

    def test_empty_returns_zeros(self, collector: MetricsCollector):
        stats = collector.get_latency_stats()
        assert stats.sample_count == 0
        assert stats.min_ms == 0.0
        assert stats.max_ms == 0.0
        assert stats.avg_ms == 0.0

    def test_single_sample(self, collector: MetricsCollector):
        collector.record_event_processed("t", "doc_t", 15.0)
        stats = collector.get_latency_stats()
        assert stats.sample_count == 1
        assert stats.min_ms == 15.0
        assert stats.max_ms == 15.0
        assert stats.avg_ms == 15.0
        assert stats.p50_ms == 15.0

    def test_min_max_avg(self, collector: MetricsCollector):
        latencies = [10.0, 20.0, 30.0, 40.0, 50.0]
        for lat in latencies:
            collector.record_event_processed("t", "doc_t", lat)
        stats = collector.get_latency_stats()
        assert stats.min_ms == 10.0
        assert stats.max_ms == 50.0
        assert stats.avg_ms == 30.0

    def test_p50_median(self, collector: MetricsCollector):
        # 10 samples: [1,2,3,4,5,6,7,8,9,10]
        for i in range(1, 11):
            collector.record_event_processed("t", "doc_t", float(i))
        stats = collector.get_latency_stats()
        # p50 = sorted_samples[10 // 2] = sorted_samples[5] = 6.0
        assert stats.p50_ms == 6.0

    def test_p95_with_enough_samples(self, collector: MetricsCollector):
        # Need >= 20 samples for p95 calculation
        for i in range(1, 101):
            collector.record_event_processed("t", "doc_t", float(i))
        stats = collector.get_latency_stats()
        # p95 = sorted_samples[int(100 * 0.95)] = sorted_samples[95] = 96.0
        assert stats.p95_ms == 96.0
        assert stats.sample_count == 100

    def test_p95_with_few_samples_uses_max(self, collector: MetricsCollector):
        """When fewer than 20 samples, p95 falls back to the last element."""
        for i in range(1, 6):
            collector.record_event_processed("t", "doc_t", float(i))
        stats = collector.get_latency_stats()
        assert stats.p95_ms == 5.0  # max value

    def test_p99_with_enough_samples(self, collector: MetricsCollector):
        # Need >= 100 samples for p99 calculation
        for i in range(1, 101):
            collector.record_event_processed("t", "doc_t", float(i))
        stats = collector.get_latency_stats()
        # p99 = sorted_samples[int(100 * 0.99)] = sorted_samples[99] = 100.0
        assert stats.p99_ms == 100.0

    def test_p99_with_few_samples_uses_max(self, collector: MetricsCollector):
        for i in range(1, 11):
            collector.record_event_processed("t", "doc_t", float(i))
        stats = collector.get_latency_stats()
        assert stats.p99_ms == 10.0  # max value

    def test_sliding_window_respects_max_samples(self):
        """Latency samples are capped at max_latency_samples."""
        collector = MetricsCollector(max_latency_samples=5)
        for i in range(10):
            collector.record_event_processed("t", "doc_t", float(i))
        stats = collector.get_latency_stats()
        assert stats.sample_count == 5
        # Only the last 5 samples should remain: [5, 6, 7, 8, 9]
        assert stats.min_ms == 5.0
        assert stats.max_ms == 9.0


# =========================================================================
# get_events_per_second
# =========================================================================


class TestGetEventsPerSecond:
    """Tests for EPS calculation."""

    def test_returns_zero_when_elapsed_under_one_second(self, collector: MetricsCollector):
        """When less than 1 second has elapsed, returns 0.0."""
        eps = collector.get_events_per_second()
        assert eps == 0.0

    def test_calculates_rate_after_processing(self, collector: MetricsCollector):
        """After processing events, EPS reflects the delta."""
        # Backdate the last measurement time so elapsed > 1 second
        collector._last_events_time = datetime.now(UTC) - timedelta(seconds=2)
        collector._last_events_count = 0
        collector.events_processed = 10

        eps = collector.get_events_per_second()
        # 10 events in ~2 seconds = ~5 EPS
        assert eps > 0.0
        assert eps <= 10.0  # upper bound sanity check

    def test_updates_baseline_after_call(self, collector: MetricsCollector):
        """Calling get_events_per_second resets the baseline for next call."""
        collector._last_events_time = datetime.now(UTC) - timedelta(seconds=2)
        collector._last_events_count = 0
        collector.events_processed = 10

        collector.get_events_per_second()

        # Baseline should now be updated
        assert collector._last_events_count == 10

    def test_second_call_reflects_only_new_events(self, collector: MetricsCollector):
        """Consecutive calls compute delta from last measurement."""
        collector._last_events_time = datetime.now(UTC) - timedelta(seconds=2)
        collector._last_events_count = 0
        collector.events_processed = 10

        collector.get_events_per_second()  # resets baseline

        # Simulate no new events and elapsed < 1s
        eps = collector.get_events_per_second()
        assert eps == 0.0  # elapsed < 1 second, returns 0


# =========================================================================
# get_template_stats
# =========================================================================


class TestGetTemplateStats:
    """Tests for per-template aggregation."""

    def test_empty_when_no_events(self, collector: MetricsCollector):
        assert collector.get_template_stats() == []

    def test_returns_stats_for_processed_templates(self, collector: MetricsCollector):
        collector.record_event_processed("person", "doc_person", 5.0)
        collector.record_event_processed("address", "doc_address", 3.0)
        stats = collector.get_template_stats()
        assert len(stats) == 2
        values = {s.template_value for s in stats}
        assert values == {"person", "address"}

    def test_includes_failed_template_stats(self, collector: MetricsCollector):
        collector.record_event_failed("person", "doc_person", "error", "msg")
        stats = collector.get_template_stats()
        assert len(stats) == 1
        assert stats[0].documents_failed == 1

    def test_mixed_success_and_failure(self, collector: MetricsCollector):
        collector.record_event_processed("person", "doc_person", 5.0)
        collector.record_event_processed("person", "doc_person", 8.0)
        collector.record_event_failed("person", "doc_person", "error", "msg")
        stats = collector.get_template_stats()
        assert len(stats) == 1
        assert stats[0].documents_synced == 2
        assert stats[0].documents_failed == 1


# =========================================================================
# get_errors_by_type
# =========================================================================


class TestGetErrorsByType:
    """Tests for error type breakdown."""

    def test_empty_when_no_errors(self, collector: MetricsCollector):
        assert collector.get_errors_by_type() == {}

    def test_returns_error_counts(self, collector: MetricsCollector):
        collector.record_event_failed(None, None, "invalid_event", "a")
        collector.record_event_failed(None, None, "invalid_event", "b")
        collector.record_event_failed(None, None, "template_not_found", "c")
        errors = collector.get_errors_by_type()
        assert errors == {"invalid_event": 2, "template_not_found": 1}

    def test_returns_copy_not_reference(self, collector: MetricsCollector):
        collector.record_event_failed(None, None, "error", "msg")
        errors = collector.get_errors_by_type()
        errors["error"] = 999
        assert collector.get_errors_by_type()["error"] == 1


# =========================================================================
# get_uptime_seconds
# =========================================================================


class TestGetUptimeSeconds:
    """Tests for uptime calculation."""

    def test_uptime_is_positive(self, collector: MetricsCollector):
        assert collector.get_uptime_seconds() >= 0.0

    def test_uptime_increases_over_time(self, collector: MetricsCollector):
        # Backdate started_at to ensure measurable uptime
        collector.started_at = datetime.now(UTC) - timedelta(seconds=10)
        uptime = collector.get_uptime_seconds()
        assert uptime >= 9.0  # allow small timing variance

    def test_uptime_reflects_started_at(self, collector: MetricsCollector):
        collector.started_at = datetime.now(UTC) - timedelta(hours=1)
        uptime = collector.get_uptime_seconds()
        assert 3590 < uptime < 3610  # approximately 3600 seconds


# =========================================================================
# Alert Configuration
# =========================================================================


class TestAlertConfig:
    """Tests for alert configuration get/update."""

    def test_default_config_is_enabled(self, collector: MetricsCollector):
        config = collector.get_alert_config()
        assert config.enabled is True

    def test_update_alert_config(self, collector: MetricsCollector):
        new_config = AlertConfig(
            enabled=False,
            check_interval_seconds=60,
            thresholds=AlertThresholds(queue_lag_warning=200),
        )
        collector.update_alert_config(new_config)
        config = collector.get_alert_config()
        assert config.enabled is False
        assert config.check_interval_seconds == 60
        assert config.thresholds.queue_lag_warning == 200

    def test_update_preserves_full_config(self, collector: MetricsCollector):
        new_config = AlertConfig(
            enabled=True,
            webhook_url="https://hooks.example.com/alert",
            webhook_headers={"Authorization": "Bearer abc"},
        )
        collector.update_alert_config(new_config)
        config = collector.get_alert_config()
        assert config.webhook_url == "https://hooks.example.com/alert"
        assert config.webhook_headers["Authorization"] == "Bearer abc"


# =========================================================================
# check_alerts - Queue Lag
# =========================================================================


class TestCheckAlertsQueueLag:
    """Tests for queue lag alert conditions."""

    @pytest.mark.asyncio
    async def test_queue_lag_warning(self, collector: MetricsCollector):
        """Pending messages exceeding warning threshold triggers warning alert."""
        consumer = ConsumerInfo(
            stream_name="WIP_EVENTS",
            consumer_name="reporting-sync",
            pending_messages=150,  # default warning = 100
            ack_pending=0,
        )
        alerts = await collector.check_alerts(consumer, True, True)
        assert len(alerts) == 1
        assert alerts[0].alert_type == AlertType.QUEUE_LAG
        assert alerts[0].severity == AlertSeverity.WARNING

    @pytest.mark.asyncio
    async def test_queue_lag_critical(self, collector: MetricsCollector):
        """Pending messages exceeding critical threshold triggers critical alert."""
        consumer = ConsumerInfo(
            stream_name="WIP_EVENTS",
            consumer_name="reporting-sync",
            pending_messages=500,
            ack_pending=600,  # total pending = 1100, default critical = 1000
        )
        alerts = await collector.check_alerts(consumer, True, True)
        lag_alerts = [a for a in alerts if a.alert_type == AlertType.QUEUE_LAG]
        assert len(lag_alerts) == 1
        assert lag_alerts[0].severity == AlertSeverity.CRITICAL

    @pytest.mark.asyncio
    async def test_queue_lag_below_threshold_no_alert(self, collector: MetricsCollector):
        """Pending messages below warning threshold triggers no alert."""
        consumer = ConsumerInfo(
            stream_name="WIP_EVENTS",
            consumer_name="reporting-sync",
            pending_messages=10,
            ack_pending=5,
        )
        alerts = await collector.check_alerts(consumer, True, True)
        lag_alerts = [a for a in alerts if a.alert_type == AlertType.QUEUE_LAG]
        assert len(lag_alerts) == 0

    @pytest.mark.asyncio
    async def test_queue_lag_resolution(self, collector: MetricsCollector):
        """Alert resolves when pending messages drop below threshold."""
        # First trigger a warning
        consumer_high = ConsumerInfo(
            stream_name="WIP_EVENTS",
            consumer_name="reporting-sync",
            pending_messages=150,
            ack_pending=0,
        )
        await collector.check_alerts(consumer_high, True, True)
        assert len(collector.get_active_alerts()) == 1

        # Now messages drop below threshold
        consumer_low = ConsumerInfo(
            stream_name="WIP_EVENTS",
            consumer_name="reporting-sync",
            pending_messages=5,
            ack_pending=0,
        )
        await collector.check_alerts(consumer_low, True, True)
        assert len(collector.get_active_alerts()) == 0
        assert len(collector.get_resolved_alerts()) == 1
        resolved = collector.get_resolved_alerts()[0]
        assert resolved.alert_type == AlertType.QUEUE_LAG
        assert resolved.resolved_at is not None


# =========================================================================
# check_alerts - Connection Lost
# =========================================================================


class TestCheckAlertsConnectionLost:
    """Tests for connection loss alert conditions."""

    @pytest.mark.asyncio
    async def test_nats_connection_lost(self, collector: MetricsCollector):
        alerts = await collector.check_alerts(None, nats_connected=False, postgres_connected=True)
        conn_alerts = [a for a in alerts if a.alert_type == AlertType.CONNECTION_LOST]
        assert len(conn_alerts) == 1
        assert conn_alerts[0].severity == AlertSeverity.CRITICAL
        assert "NATS" in conn_alerts[0].message

    @pytest.mark.asyncio
    async def test_postgres_connection_lost(self, collector: MetricsCollector):
        alerts = await collector.check_alerts(None, nats_connected=True, postgres_connected=False)
        conn_alerts = [a for a in alerts if a.alert_type == AlertType.CONNECTION_LOST]
        assert len(conn_alerts) == 1
        assert conn_alerts[0].severity == AlertSeverity.CRITICAL
        assert "PostgreSQL" in conn_alerts[0].message

    @pytest.mark.asyncio
    async def test_connection_restored_resolves_alert(self, collector: MetricsCollector):
        # Trigger connection lost
        await collector.check_alerts(None, nats_connected=False, postgres_connected=True)
        assert len(collector.get_active_alerts()) == 1

        # Connection restored
        await collector.check_alerts(None, nats_connected=True, postgres_connected=True)
        assert len(collector.get_active_alerts()) == 0
        assert len(collector.get_resolved_alerts()) == 1

    @pytest.mark.asyncio
    async def test_no_duplicate_connection_alert(self, collector: MetricsCollector):
        """Second check while still disconnected should not create duplicate."""
        await collector.check_alerts(None, nats_connected=False, postgres_connected=True)
        alerts2 = await collector.check_alerts(None, nats_connected=False, postgres_connected=True)
        # No new alerts on second check since alert already exists
        conn_alerts = [a for a in alerts2 if a.alert_type == AlertType.CONNECTION_LOST]
        assert len(conn_alerts) == 0
        assert len(collector.get_active_alerts()) == 1


# =========================================================================
# check_alerts - Processing Stalled
# =========================================================================


class TestCheckAlertsProcessingStalled:
    """Tests for processing stalled alert conditions."""

    @pytest.mark.asyncio
    async def test_stall_warning(self, collector: MetricsCollector):
        """No events for stall_warning_seconds triggers warning."""
        # Set last_event_at to well past the warning threshold (default 300s)
        collector.last_event_at = datetime.now(UTC) - timedelta(seconds=350)
        alerts = await collector.check_alerts(None, True, True)
        stall_alerts = [a for a in alerts if a.alert_type == AlertType.PROCESSING_STALLED]
        assert len(stall_alerts) == 1
        assert stall_alerts[0].severity == AlertSeverity.WARNING

    @pytest.mark.asyncio
    async def test_stall_critical(self, collector: MetricsCollector):
        """No events for stall_critical_seconds triggers critical."""
        collector.last_event_at = datetime.now(UTC) - timedelta(seconds=700)
        alerts = await collector.check_alerts(None, True, True)
        stall_alerts = [a for a in alerts if a.alert_type == AlertType.PROCESSING_STALLED]
        assert len(stall_alerts) == 1
        assert stall_alerts[0].severity == AlertSeverity.CRITICAL

    @pytest.mark.asyncio
    async def test_stall_resolves_when_event_received(self, collector: MetricsCollector):
        """Stall alert resolves when a recent event is recorded."""
        # Trigger stall
        collector.last_event_at = datetime.now(UTC) - timedelta(seconds=350)
        await collector.check_alerts(None, True, True)
        assert len(collector.get_active_alerts()) == 1

        # Recent event
        collector.last_event_at = datetime.now(UTC)
        await collector.check_alerts(None, True, True)
        assert len(collector.get_active_alerts()) == 0
        assert len(collector.get_resolved_alerts()) == 1

    @pytest.mark.asyncio
    async def test_no_stall_alert_when_no_events_ever(self, collector: MetricsCollector):
        """No stall alert if no events have ever been processed (last_event_at is None)."""
        collector.last_event_at = None
        alerts = await collector.check_alerts(None, True, True)
        stall_alerts = [a for a in alerts if a.alert_type == AlertType.PROCESSING_STALLED]
        assert len(stall_alerts) == 0


# =========================================================================
# check_alerts - Error Rate
# =========================================================================


class TestCheckAlertsErrorRate:
    """Tests for error rate alert conditions."""

    @pytest.mark.asyncio
    async def test_error_rate_warning(self, collector: MetricsCollector):
        """Error rate exceeding warning threshold triggers warning."""
        # Default warning is 5%. Set 6% error rate.
        collector.events_processed = 94
        collector.events_failed = 6  # 6/(94+6) = 6%
        alerts = await collector.check_alerts(None, True, True)
        error_alerts = [a for a in alerts if a.alert_type == AlertType.ERROR_RATE]
        assert len(error_alerts) == 1
        assert error_alerts[0].severity == AlertSeverity.WARNING

    @pytest.mark.asyncio
    async def test_error_rate_critical(self, collector: MetricsCollector):
        """Error rate exceeding critical threshold triggers critical."""
        # Default critical is 20%. Set 25% error rate.
        collector.events_processed = 75
        collector.events_failed = 25  # 25/(75+25) = 25%
        alerts = await collector.check_alerts(None, True, True)
        error_alerts = [a for a in alerts if a.alert_type == AlertType.ERROR_RATE]
        assert len(error_alerts) == 1
        assert error_alerts[0].severity == AlertSeverity.CRITICAL

    @pytest.mark.asyncio
    async def test_error_rate_below_threshold(self, collector: MetricsCollector):
        """Error rate below warning threshold triggers no alert."""
        collector.events_processed = 99
        collector.events_failed = 1  # 1%
        alerts = await collector.check_alerts(None, True, True)
        error_alerts = [a for a in alerts if a.alert_type == AlertType.ERROR_RATE]
        assert len(error_alerts) == 0

    @pytest.mark.asyncio
    async def test_error_rate_resolves_when_rate_drops(self, collector: MetricsCollector):
        """Error rate alert resolves when error rate drops below threshold."""
        # Trigger error rate alert
        collector.events_processed = 80
        collector.events_failed = 20  # 20%
        await collector.check_alerts(None, True, True)
        assert len(collector.get_active_alerts()) >= 1

        # Error rate drops (more successful events come in)
        collector.events_processed = 980
        collector.events_failed = 20  # 2%
        await collector.check_alerts(None, True, True)
        error_active = [
            a for a in collector.get_active_alerts()
            if a.alert_type == AlertType.ERROR_RATE
        ]
        assert len(error_active) == 0

    @pytest.mark.asyncio
    async def test_no_error_rate_alert_with_zero_events(self, collector: MetricsCollector):
        """No alert when no events have been processed at all."""
        alerts = await collector.check_alerts(None, True, True)
        error_alerts = [a for a in alerts if a.alert_type == AlertType.ERROR_RATE]
        assert len(error_alerts) == 0


# =========================================================================
# Alert Resolution
# =========================================================================


class TestAlertResolution:
    """Tests for alert lifecycle (active vs resolved)."""

    @pytest.mark.asyncio
    async def test_get_active_alerts_empty_initially(self, collector: MetricsCollector):
        assert collector.get_active_alerts() == []

    @pytest.mark.asyncio
    async def test_get_resolved_alerts_empty_initially(self, collector: MetricsCollector):
        assert collector.get_resolved_alerts() == []

    @pytest.mark.asyncio
    async def test_active_alert_appears_in_get_active(self, collector: MetricsCollector):
        await collector.check_alerts(None, nats_connected=False, postgres_connected=True)
        active = collector.get_active_alerts()
        assert len(active) == 1
        assert active[0].alert_type == AlertType.CONNECTION_LOST

    @pytest.mark.asyncio
    async def test_resolved_alert_moves_from_active_to_resolved(self, collector: MetricsCollector):
        # Create then resolve
        await collector.check_alerts(None, nats_connected=False, postgres_connected=True)
        await collector.check_alerts(None, nats_connected=True, postgres_connected=True)
        assert len(collector.get_active_alerts()) == 0
        assert len(collector.get_resolved_alerts()) == 1

    @pytest.mark.asyncio
    async def test_resolved_alert_has_resolved_at_timestamp(self, collector: MetricsCollector):
        await collector.check_alerts(None, nats_connected=False, postgres_connected=True)
        await collector.check_alerts(None, nats_connected=True, postgres_connected=True)
        resolved = collector.get_resolved_alerts()[0]
        assert resolved.resolved_at is not None
        assert resolved.triggered_at < resolved.resolved_at

    @pytest.mark.asyncio
    async def test_alerts_disabled_returns_empty(self, collector: MetricsCollector):
        """When alerts are disabled, check_alerts returns no alerts."""
        collector.update_alert_config(AlertConfig(enabled=False))
        # Conditions that would otherwise trigger alerts
        collector.last_event_at = datetime.now(UTC) - timedelta(seconds=700)
        alerts = await collector.check_alerts(None, nats_connected=False, postgres_connected=False)
        assert alerts == []

    @pytest.mark.asyncio
    async def test_resolved_alerts_capped_at_max_history(self):
        """Resolved alerts deque respects max_alerts_history limit."""
        collector = MetricsCollector(max_alerts_history=3)
        # Create and resolve 5 connection alerts by toggling connection
        for _ in range(5):
            await collector.check_alerts(None, nats_connected=False, postgres_connected=True)
            await collector.check_alerts(None, nats_connected=True, postgres_connected=True)
        # Only last 3 resolved alerts should be retained
        assert len(collector.get_resolved_alerts()) == 3

    @pytest.mark.asyncio
    async def test_multiple_alert_types_simultaneously(self, collector: MetricsCollector):
        """Multiple different alert conditions can fire in one check."""
        collector.last_event_at = datetime.now(UTC) - timedelta(seconds=700)
        collector.events_processed = 75
        collector.events_failed = 25  # 25% error rate

        consumer = ConsumerInfo(
            stream_name="WIP_EVENTS",
            consumer_name="reporting-sync",
            pending_messages=2000,
            ack_pending=0,
        )
        alerts = await collector.check_alerts(consumer, nats_connected=True, postgres_connected=True)

        alert_types = {a.alert_type for a in alerts}
        assert AlertType.PROCESSING_STALLED in alert_types
        assert AlertType.ERROR_RATE in alert_types
        assert AlertType.QUEUE_LAG in alert_types
        assert len(collector.get_active_alerts()) >= 3
