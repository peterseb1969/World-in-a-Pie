"""
Metrics collection for the Reporting Sync service.

Tracks processing latency, per-template stats, and error counts.
"""

import asyncio
import logging
import statistics
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

import httpx

from .models import (
    Alert,
    AlertConfig,
    AlertSeverity,
    AlertThresholds,
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
        self.started_at = datetime.now(timezone.utc)

        # Event counters
        self.events_processed = 0
        self.events_failed = 0
        self._last_events_count = 0
        self._last_events_time = datetime.now(timezone.utc)

        # Latency tracking (sliding window)
        self._latency_samples: deque[float] = deque(maxlen=max_latency_samples)

        # Per-template stats
        self._template_stats: dict[str, PerTemplateStats] = {}

        # Error breakdown
        self._errors_by_type: dict[str, int] = {}

        # Last event timestamp
        self.last_event_at: datetime | None = None

        # Alert state
        self._alert_config = AlertConfig()
        self._active_alerts: dict[str, Alert] = {}
        self._resolved_alerts: deque[Alert] = deque(maxlen=max_alerts_history)
        self._alert_check_task: asyncio.Task | None = None

    def record_event_processed(
        self,
        template_value: str,
        table_name: str,
        latency_ms: float,
    ) -> None:
        """Record a successfully processed event."""
        self.events_processed += 1
        self.last_event_at = datetime.now(timezone.utc)
        self._latency_samples.append(latency_ms)

        # Update template stats
        if template_value not in self._template_stats:
            self._template_stats[template_value] = PerTemplateStats(
                template_value=template_value,
                table_name=table_name,
            )

        stats = self._template_stats[template_value]
        stats.documents_synced += 1
        stats.last_sync_at = datetime.now(timezone.utc)

    def record_event_failed(
        self,
        template_value: str | None,
        table_name: str | None,
        error_type: str,
        error_message: str,
    ) -> None:
        """Record a failed event."""
        self.events_failed += 1
        self.last_event_at = datetime.now(timezone.utc)

        # Track error type
        self._errors_by_type[error_type] = self._errors_by_type.get(error_type, 0) + 1

        # Update template stats if known
        if template_value:
            if template_value not in self._template_stats:
                self._template_stats[template_value] = PerTemplateStats(
                    template_value=template_value,
                    table_name=table_name or f"doc_{template_value.lower()}",
                )

            stats = self._template_stats[template_value]
            stats.documents_failed += 1
            stats.last_error = error_message
            stats.last_error_at = datetime.now(timezone.utc)

    def record_event_skipped(self, template_value: str, reason: str) -> None:
        """Record a skipped event (sync disabled, etc.)."""
        self.events_processed += 1  # Still counts as processed
        self.last_event_at = datetime.now(timezone.utc)
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
        now = datetime.now(timezone.utc)
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
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()

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
            triggered_at=datetime.now(timezone.utc),
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
            alert.resolved_at = datetime.now(timezone.utc)
            self._resolved_alerts.append(alert)
            logger.info(f"Alert resolved: {alert.message}")
            return alert
        return None

    async def check_alerts(
        self,
        consumer_info: ConsumerInfo | None,
        nats_connected: bool,
        postgres_connected: bool,
    ) -> list[Alert]:
        """
        Check all alert conditions and return any new alerts triggered.

        This should be called periodically by the alert check task.
        """
        if not self._alert_config.enabled:
            return []

        new_alerts: list[Alert] = []
        thresholds = self._alert_config.thresholds

        # 1. Check connection alerts
        if not nats_connected:
            if AlertType.CONNECTION_LOST.value not in self._active_alerts:
                alert = self._create_alert(
                    AlertType.CONNECTION_LOST,
                    AlertSeverity.CRITICAL,
                    "Lost connection to NATS",
                    {"connection": "nats"},
                )
                new_alerts.append(alert)
        elif not postgres_connected:
            if AlertType.CONNECTION_LOST.value not in self._active_alerts:
                alert = self._create_alert(
                    AlertType.CONNECTION_LOST,
                    AlertSeverity.CRITICAL,
                    "Lost connection to PostgreSQL",
                    {"connection": "postgres"},
                )
                new_alerts.append(alert)
        else:
            self._resolve_alert(AlertType.CONNECTION_LOST)

        # 2. Check queue lag alerts
        if consumer_info:
            pending = consumer_info.pending_messages + consumer_info.ack_pending

            if pending >= thresholds.queue_lag_critical:
                if AlertType.QUEUE_LAG.value not in self._active_alerts or \
                   self._active_alerts[AlertType.QUEUE_LAG.value].severity != AlertSeverity.CRITICAL:
                    alert = self._create_alert(
                        AlertType.QUEUE_LAG,
                        AlertSeverity.CRITICAL,
                        f"Queue lag critical: {pending} pending messages",
                        {"pending_messages": pending, "threshold": thresholds.queue_lag_critical},
                    )
                    new_alerts.append(alert)
            elif pending >= thresholds.queue_lag_warning:
                if AlertType.QUEUE_LAG.value not in self._active_alerts:
                    alert = self._create_alert(
                        AlertType.QUEUE_LAG,
                        AlertSeverity.WARNING,
                        f"Queue lag warning: {pending} pending messages",
                        {"pending_messages": pending, "threshold": thresholds.queue_lag_warning},
                    )
                    new_alerts.append(alert)
            else:
                self._resolve_alert(AlertType.QUEUE_LAG)

        # 3. Check processing stalled alert
        if self.last_event_at:
            seconds_since_event = (
                datetime.now(timezone.utc) - self.last_event_at
            ).total_seconds()

            if seconds_since_event >= thresholds.stall_critical_seconds:
                if AlertType.PROCESSING_STALLED.value not in self._active_alerts or \
                   self._active_alerts[AlertType.PROCESSING_STALLED.value].severity != AlertSeverity.CRITICAL:
                    alert = self._create_alert(
                        AlertType.PROCESSING_STALLED,
                        AlertSeverity.CRITICAL,
                        f"Processing stalled: no events for {int(seconds_since_event)}s",
                        {"seconds_since_event": seconds_since_event},
                    )
                    new_alerts.append(alert)
            elif seconds_since_event >= thresholds.stall_warning_seconds:
                if AlertType.PROCESSING_STALLED.value not in self._active_alerts:
                    alert = self._create_alert(
                        AlertType.PROCESSING_STALLED,
                        AlertSeverity.WARNING,
                        f"Processing may be stalled: no events for {int(seconds_since_event)}s",
                        {"seconds_since_event": seconds_since_event},
                    )
                    new_alerts.append(alert)
            else:
                self._resolve_alert(AlertType.PROCESSING_STALLED)

        # 4. Check error rate (simple: errors in last minute based on ratio)
        total = self.events_processed + self.events_failed
        if total > 0:
            error_rate = (self.events_failed / total) * 100
            if error_rate >= thresholds.error_rate_critical:
                if AlertType.ERROR_RATE.value not in self._active_alerts or \
                   self._active_alerts[AlertType.ERROR_RATE.value].severity != AlertSeverity.CRITICAL:
                    alert = self._create_alert(
                        AlertType.ERROR_RATE,
                        AlertSeverity.CRITICAL,
                        f"Error rate critical: {error_rate:.1f}%",
                        {"error_rate": error_rate, "threshold": thresholds.error_rate_critical},
                    )
                    new_alerts.append(alert)
            elif error_rate >= thresholds.error_rate_warning:
                if AlertType.ERROR_RATE.value not in self._active_alerts:
                    alert = self._create_alert(
                        AlertType.ERROR_RATE,
                        AlertSeverity.WARNING,
                        f"Error rate elevated: {error_rate:.1f}%",
                        {"error_rate": error_rate, "threshold": thresholds.error_rate_warning},
                    )
                    new_alerts.append(alert)
            else:
                self._resolve_alert(AlertType.ERROR_RATE)

        # Send webhook notifications for new alerts
        if new_alerts and self._alert_config.webhook_url:
            await self._send_webhook_notifications(new_alerts)

        return new_alerts

    async def _send_webhook_notifications(self, alerts: list[Alert]) -> None:
        """Send webhook notifications for new alerts."""
        if not self._alert_config.webhook_url:
            return

        payload = {
            "service": "wip-reporting-sync",
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
