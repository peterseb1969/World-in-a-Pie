"""
Metrics collection for the Reporting Sync service.

Tracks processing latency, per-template stats, and error counts.
"""

import asyncio
import logging
import statistics
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

import httpx

from .models import (
    Alert,
    AlertConfig,
    AlertSeverity,
    AlertType,
    ConsumerInfo,
    LatencyStats,
    MetricsResponse,
    PerTemplateStats,
)

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Collects and calculates metrics for the sync service."""

    def __init__(self, max_latency_samples: int = 1000, max_alerts_history: int = 100):
        # Timing
        self.started_at = datetime.now(UTC)

        # Event counters
        self.events_processed = 0
        self.events_failed = 0
        # (processed, failed) at the previous alert check: the error-rate window.
        self._rate_window: tuple[int, int] = (0, 0)
        self._last_events_count = 0
        self._last_events_time = datetime.now(UTC)

        # Latency tracking (sliding window)
        self._latency_samples: deque[float] = deque(maxlen=max_latency_samples)

        # Per-template stats, keyed by (namespace, template value): the same
        # value in two namespaces is two templates with two tables.
        self._template_stats: dict[tuple[str | None, str], PerTemplateStats] = {}

        # Error breakdown
        self._errors_by_type: dict[str, int] = {}

        # Last event timestamp
        self.last_event_at: datetime | None = None

        # Alert state
        self._alert_config = AlertConfig()
        self._active_alerts: dict[str, Alert] = {}
        self._resolved_alerts: deque[Alert] = deque(maxlen=max_alerts_history)
        self._alert_check_task: asyncio.Task | None = None

    def _stats_for(
        self, namespace: str | None, template_value: str, table_name: str | None,
    ) -> PerTemplateStats:
        """The stats row of one (namespace, template value). Its table_name
        follows the latest event that named one (a new template version
        writes a new table)."""
        key = (namespace, template_value)
        stats = self._template_stats.get(key)
        if stats is None:
            stats = self._template_stats[key] = PerTemplateStats(
                template_value=template_value, namespace=namespace,
                table_name=table_name,
            )
        elif table_name:
            stats.table_name = table_name
        return stats

    def record_event_processed(
        self,
        template_value: str,
        table_name: str,
        latency_ms: float,
        namespace: str | None = None,
    ) -> None:
        """Record a successfully processed event."""
        self.events_processed += 1
        self.last_event_at = datetime.now(UTC)
        self._latency_samples.append(latency_ms)

        stats = self._stats_for(namespace, template_value, table_name)
        stats.documents_synced += 1
        stats.last_sync_at = datetime.now(UTC)

    def record_event_failed(
        self,
        template_value: str | None,
        table_name: str | None,
        error_type: str,
        error_message: str,
        namespace: str | None = None,
    ) -> None:
        """Record a failed event."""
        self.events_failed += 1
        self.last_event_at = datetime.now(UTC)

        # Track error type
        self._errors_by_type[error_type] = self._errors_by_type.get(error_type, 0) + 1

        # Update template stats if known
        if template_value:
            stats = self._stats_for(namespace, template_value, table_name)
            stats.documents_failed += 1
            stats.last_error = error_message
            stats.last_error_at = datetime.now(UTC)

    def record_event_skipped(self, template_value: str, reason: str) -> None:
        """Record a skipped event (sync disabled, etc.)."""
        self.events_processed += 1  # Still counts as processed
        self.last_event_at = datetime.now(UTC)
        logger.debug(f"Event skipped for {template_value}: {reason}")

    def get_latency_stats(self) -> LatencyStats:
        """Calculate latency statistics from samples."""
        if not self._latency_samples:
            return LatencyStats()

        samples = list(self._latency_samples)
        sorted_samples = sorted(samples)
        n = len(sorted_samples)

        return LatencyStats(
            sample_count=n,
            min_ms=min(samples),
            max_ms=max(samples),
            avg_ms=statistics.mean(samples),
            p50_ms=sorted_samples[n // 2],
            p95_ms=sorted_samples[int(n * 0.95)] if n >= 20 else sorted_samples[-1],
            p99_ms=sorted_samples[int(n * 0.99)] if n >= 100 else sorted_samples[-1],
        )

    def get_events_per_second(self) -> float:
        """Calculate current events per second rate."""
        now = datetime.now(UTC)
        elapsed = (now - self._last_events_time).total_seconds()

        if elapsed < 1:
            return 0.0

        events_delta = self.events_processed - self._last_events_count
        rate = events_delta / elapsed

        # Update for next calculation
        self._last_events_count = self.events_processed
        self._last_events_time = now

        return round(rate, 2)

    def get_template_stats(self) -> list[PerTemplateStats]:
        """Get all per-template statistics."""
        return list(self._template_stats.values())

    def get_errors_by_type(self) -> dict[str, int]:
        """Get error counts by type."""
        return dict(self._errors_by_type)

    def get_uptime_seconds(self) -> float:
        """Get service uptime in seconds."""
        return (datetime.now(UTC) - self.started_at).total_seconds()

    def build_metrics_response(
        self,
        nats_connected: bool,
        postgres_connected: bool,
        consumer_info: ConsumerInfo | None = None,
    ) -> MetricsResponse:
        """Build a complete metrics response."""
        return MetricsResponse(
            started_at=self.started_at,
            uptime_seconds=self.get_uptime_seconds(),
            nats_connected=nats_connected,
            postgres_connected=postgres_connected,
            events_processed=self.events_processed,
            events_failed=self.events_failed,
            events_per_second=self.get_events_per_second(),
            consumer_info=consumer_info,
            processing_latency=self.get_latency_stats(),
            template_stats=self.get_template_stats(),
            errors_by_type=self.get_errors_by_type(),
        )

    # =========================================================================
    # ALERT MANAGEMENT
    # =========================================================================

    def get_alert_config(self) -> AlertConfig:
        """Get current alert configuration."""
        return self._alert_config

    def update_alert_config(self, config: AlertConfig) -> None:
        """Update alert configuration."""
        self._alert_config = config
        logger.info(f"Alert config updated: enabled={config.enabled}")

    def get_active_alerts(self) -> list[Alert]:
        """Get currently active alerts."""
        return list(self._active_alerts.values())

    def get_resolved_alerts(self) -> list[Alert]:
        """Get recently resolved alerts."""
        return list(self._resolved_alerts)

    def _create_alert(
        self,
        alert_type: AlertType,
        severity: AlertSeverity,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> Alert:
        """Create and track a new alert."""
        alert_id = f"{alert_type.value}-{uuid.uuid4().hex[:8]}"
        alert = Alert(
            alert_id=alert_id,
            alert_type=alert_type,
            severity=severity,
            message=message,
            triggered_at=datetime.now(UTC),
            details=details or {},
        )
        self._active_alerts[alert_type.value] = alert
        logger.warning(f"Alert triggered: [{severity.value}] {message}")
        return alert

    def _resolve_alert(self, alert_type: AlertType) -> Alert | None:
        """Resolve an active alert."""
        key = alert_type.value
        if key in self._active_alerts:
            alert = self._active_alerts.pop(key)
            alert.resolved_at = datetime.now(UTC)
            self._resolved_alerts.append(alert)
            logger.info(f"Alert resolved: {alert.message}")
            return alert
        return None

    async def check_alerts(
        self,
        consumer_info: ConsumerInfo | None,
        nats_connected: bool,
        postgres_connected: bool,
        advance_window: bool = True,
    ) -> list[Alert]:
        """
        Check all alert conditions and return any new alerts triggered.

        This should be called periodically by the alert check task. A manual
        check (POST /alerts/test) passes ``advance_window=False``: it reads
        the error rate over the events since the loop's last check without
        moving that window, so it neither consumes the loop's samples nor
        leaves the loop an empty window that skips the error-rate check.
        """
        if not self._alert_config.enabled:
            return []

        thresholds = self._alert_config.thresholds
        new_alerts: list[Alert | None] = [
            self._connection_alert(nats_connected, postgres_connected),
        ]

        if consumer_info:
            pending = consumer_info.pending_messages + consumer_info.ack_pending
            new_alerts.append(self._tiered_alert(
                AlertType.QUEUE_LAG, pending,
                thresholds.queue_lag_critical, thresholds.queue_lag_warning,
                critical=(
                    f"Queue lag critical: {pending} pending messages",
                    {"pending_messages": pending, "threshold": thresholds.queue_lag_critical},
                ),
                warning=(
                    f"Queue lag warning: {pending} pending messages",
                    {"pending_messages": pending, "threshold": thresholds.queue_lag_warning},
                ),
            ))

        if self.last_event_at:
            seconds_since_event = (datetime.now(UTC) - self.last_event_at).total_seconds()
            new_alerts.append(self._tiered_alert(
                AlertType.PROCESSING_STALLED, seconds_since_event,
                thresholds.stall_critical_seconds, thresholds.stall_warning_seconds,
                critical=(
                    f"Processing stalled: no events for {int(seconds_since_event)}s",
                    {"seconds_since_event": seconds_since_event},
                ),
                warning=(
                    f"Processing may be stalled: no events for {int(seconds_since_event)}s",
                    {"seconds_since_event": seconds_since_event},
                ),
            ))

        # Error rate: the share of the events since the previous check that
        # failed. Lifetime counters would let a long clean history hide a
        # failure burst, and keep an old burst alerting for ever.
        processed = self.events_processed - self._rate_window[0]
        failed = self.events_failed - self._rate_window[1]
        if advance_window:
            self._rate_window = (self.events_processed, self.events_failed)
        if processed + failed > 0:
            error_rate = (failed / (processed + failed)) * 100
            new_alerts.append(self._tiered_alert(
                AlertType.ERROR_RATE, error_rate,
                thresholds.error_rate_critical, thresholds.error_rate_warning,
                critical=(
                    f"Error rate critical: {error_rate:.1f}%",
                    {"error_rate": error_rate, "threshold": thresholds.error_rate_critical},
                ),
                warning=(
                    f"Error rate elevated: {error_rate:.1f}%",
                    {"error_rate": error_rate, "threshold": thresholds.error_rate_warning},
                ),
            ))

        raised = [a for a in new_alerts if a is not None]
        # Send webhook notifications for new alerts
        if raised and self._alert_config.webhook_url:
            await self._send_webhook_notifications(raised)
        return raised

    def _connection_alert(self, nats_connected: bool, postgres_connected: bool) -> Alert | None:
        """A new connection-lost alert (NATS first, then PostgreSQL) unless
        one is active; with both connected, the active one is resolved."""
        if nats_connected and postgres_connected:
            self._resolve_alert(AlertType.CONNECTION_LOST)
            return None
        lost = [(c, n) for c, n, up in (
            ("nats", "NATS", nats_connected), ("postgres", "PostgreSQL", postgres_connected),
        ) if not up]
        message = f"Lost connection to {' and '.join(n for _, n in lost)}"
        details = {"connection": ",".join(c for c, _ in lost)}
        active = self._active_alerts.get(AlertType.CONNECTION_LOST.value)
        if active is not None:
            # The outage goes on; it names what is down now, not what went
            # down first.
            active.message, active.details = message, details
            return None
        return self._create_alert(
            AlertType.CONNECTION_LOST, AlertSeverity.CRITICAL, message, details,
        )

    def _tiered_alert(
        self,
        alert_type: AlertType,
        value: float,
        critical_at: float,
        warning_at: float,
        *,
        critical: tuple[str, dict[str, Any]],
        warning: tuple[str, dict[str, Any]],
    ) -> Alert | None:
        """The new alert a value raises against its two thresholds, if any.

        At the critical level an alert is raised unless a critical one is
        active (an active warning is escalated). At the warning level one is
        raised only when none of the type is active, so an active critical is
        never downgraded. Below both, the active alert is resolved.
        """
        active = self._active_alerts.get(alert_type.value)
        if value >= critical_at:
            if active is not None and active.severity == AlertSeverity.CRITICAL:
                return None
            # An escalated warning ends here: it is recorded as resolved,
            # not overwritten, so the history shows both alerts.
            self._resolve_alert(alert_type)
            return self._create_alert(alert_type, AlertSeverity.CRITICAL, *critical)
        if value >= warning_at:
            if active is not None:
                return None
            return self._create_alert(alert_type, AlertSeverity.WARNING, *warning)
        self._resolve_alert(alert_type)
        return None

    async def _send_webhook_notifications(self, alerts: list[Alert]) -> None:
        """Send webhook notifications for new alerts."""
        if not self._alert_config.webhook_url:
            return

        payload = {
            "service": "wip-reporting-sync",
            "timestamp": datetime.now(UTC).isoformat(),
            "alerts": [alert.model_dump(mode="json") for alert in alerts],
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self._alert_config.webhook_url,
                    json=payload,
                    headers=self._alert_config.webhook_headers,
                    timeout=10.0,
                )
                if response.status_code >= 400:
                    logger.error(
                        f"Webhook notification failed: {response.status_code} {response.text}"
                    )
                else:
                    logger.info(f"Webhook notification sent for {len(alerts)} alerts")
        except Exception as e:
            logger.error(f"Failed to send webhook notification: {e}")


# Global metrics instance
metrics = MetricsCollector()
