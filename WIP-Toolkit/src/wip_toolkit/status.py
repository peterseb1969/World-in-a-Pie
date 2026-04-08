"""Aggregated status check for WIP services.

CASE-26 / v1.0 Phase 2 — Observability.

Hits each WIP service's existing health/metrics endpoints, applies configurable
thresholds, and returns a structured result with an overall verdict. Designed
to run unattended from cron: cheap by default, full integrity scan opt-in.

Exit codes (used by the CLI wrapper):
    0  ok        — everything within thresholds
    1  warning   — at least one warning, no criticals
    2  critical  — at least one critical issue
    3  unknown   — at least one service unreachable, no criticals/warnings
                  (overridden if a real warning/critical is also present)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from .client import WIPClient

Severity = Literal["ok", "warning", "critical", "unknown"]

# Severity ordering for "worst wins"
_SEVERITY_RANK: dict[Severity, int] = {
    "ok": 0,
    "unknown": 1,
    "warning": 2,
    "critical": 3,
}

# Services we check for liveness. Ingest gateway is included but optional —
# many installs run without it. We track it as "unknown" if unreachable rather
# than "critical".
LIVENESS_SERVICES = [
    "registry",
    "def-store",
    "template-store",
    "document-store",
    "reporting-sync",
]
OPTIONAL_LIVENESS_SERVICES = ["ingest-gateway"]


@dataclass
class StatusThresholds:
    """Configurable thresholds for status checks.

    Defaults match the values called out in CASE-26.
    """

    failed_events_warning: int = 1  # any failed event is at least a warning
    consumer_lag_warning: int = 100
    consumer_lag_critical: int = 1000
    integrity_drift_critical: int = 1  # any integrity issue is critical
    ingest_failed_warning: int = 1


@dataclass
class CheckResult:
    """Result of a single check."""

    name: str
    severity: Severity
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class StatusReport:
    """Aggregated status across all checks."""

    overall: Severity
    checked_at: str
    checks: list[CheckResult] = field(default_factory=list)
    services_unreachable: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "checked_at": self.checked_at,
            "services_unreachable": list(self.services_unreachable),
            "checks": [
                {
                    "name": c.name,
                    "severity": c.severity,
                    "message": c.message,
                    "details": c.details,
                }
                for c in self.checks
            ],
        }

    def exit_code(self) -> int:
        if self.overall == "ok":
            return 0
        if self.overall == "warning":
            return 1
        if self.overall == "critical":
            return 2
        return 3  # unknown

    def has_problems(self) -> bool:
        """True if anything is not ok (used by --quiet mode)."""
        return self.overall != "ok"


def _worse(a: Severity, b: Severity) -> Severity:
    return a if _SEVERITY_RANK[a] >= _SEVERITY_RANK[b] else b


def collect_status(
    client: WIPClient,
    thresholds: StatusThresholds | None = None,
    *,
    include_integrity: bool = False,
    integrity_template_limit: int = 1000,
    integrity_document_limit: int = 1000,
) -> StatusReport:
    """Collect status across all WIP services and apply thresholds.

    The default mode is cron-friendly: liveness + reporting-sync metrics +
    ingest-gateway metrics. Pass ``include_integrity=True`` to also run the
    aggregated integrity check (heavier — scans templates and documents).
    """
    thresholds = thresholds or StatusThresholds()
    checks: list[CheckResult] = []
    unreachable: list[str] = []

    # 1. Liveness for required services
    for service in LIVENESS_SERVICES:
        healthy, msg = client.check_health(service)
        if healthy:
            checks.append(CheckResult(
                name=f"liveness:{service}",
                severity="ok",
                message="reachable",
            ))
        else:
            unreachable.append(service)
            checks.append(CheckResult(
                name=f"liveness:{service}",
                severity="critical",
                message=f"unreachable: {msg}",
            ))

    # 2. Liveness for optional services (downgrade to unknown, not critical)
    for service in OPTIONAL_LIVENESS_SERVICES:
        try:
            healthy, msg = client.check_health(service)
        except KeyError:
            # Service not configured in WIPConfig — skip silently
            continue
        if healthy:
            checks.append(CheckResult(
                name=f"liveness:{service}",
                severity="ok",
                message="reachable",
            ))
        else:
            unreachable.append(service)
            checks.append(CheckResult(
                name=f"liveness:{service}",
                severity="unknown",
                message=f"unreachable (optional): {msg}",
            ))

    # 3. Reporting-sync metrics — events_failed + consumer_lag
    if "reporting-sync" not in unreachable:
        checks.extend(_check_reporting_sync_metrics(client, thresholds))
        # Stall detection from reporting-sync's own alerts engine
        checks.extend(_check_reporting_sync_alerts(client))

    # 4. Ingest gateway metrics (optional, only if reachable)
    if "ingest-gateway" not in unreachable:
        try:
            checks.extend(_check_ingest_gateway_metrics(client, thresholds))
        except KeyError:
            pass

    # 5. Integrity (opt-in, heavy)
    if include_integrity and "reporting-sync" not in unreachable:
        checks.extend(_check_integrity(
            client, thresholds,
            template_limit=integrity_template_limit,
            document_limit=integrity_document_limit,
        ))

    # Roll up overall severity
    overall: Severity = "ok"
    for c in checks:
        overall = _worse(overall, c.severity)

    return StatusReport(
        overall=overall,
        checked_at=datetime.now(timezone.utc).isoformat(),
        checks=checks,
        services_unreachable=unreachable,
    )


def _check_reporting_sync_metrics(
    client: WIPClient,
    thresholds: StatusThresholds,
) -> list[CheckResult]:
    """Hit reporting-sync /metrics and apply event/lag thresholds."""
    try:
        data = client.get("reporting-sync", "/metrics")
    except Exception as e:
        return [CheckResult(
            name="reporting-sync:metrics",
            severity="unknown",
            message=f"failed to fetch /metrics: {e}",
        )]

    results: list[CheckResult] = []

    # Connection status — both NATS and Postgres must be connected
    nats_ok = bool(data.get("nats_connected"))
    pg_ok = bool(data.get("postgres_connected"))
    if not nats_ok or not pg_ok:
        missing = []
        if not nats_ok:
            missing.append("NATS")
        if not pg_ok:
            missing.append("Postgres")
        results.append(CheckResult(
            name="reporting-sync:connections",
            severity="critical",
            message=f"reporting-sync disconnected from {', '.join(missing)}",
            details={"nats_connected": nats_ok, "postgres_connected": pg_ok},
        ))
    else:
        results.append(CheckResult(
            name="reporting-sync:connections",
            severity="ok",
            message="NATS and Postgres connected",
        ))

    # Failed events
    events_failed = int(data.get("events_failed", 0) or 0)
    events_processed = int(data.get("events_processed", 0) or 0)
    if events_failed >= thresholds.failed_events_warning:
        results.append(CheckResult(
            name="reporting-sync:failed_events",
            severity="warning",
            message=f"{events_failed} failed event(s) since startup",
            details={
                "events_failed": events_failed,
                "events_processed": events_processed,
                "errors_by_type": data.get("errors_by_type") or {},
            },
        ))
    else:
        results.append(CheckResult(
            name="reporting-sync:failed_events",
            severity="ok",
            message=f"{events_processed} processed, 0 failed",
        ))

    # Consumer lag
    consumer = data.get("consumer_info") or {}
    pending = int(consumer.get("pending_messages", 0) or 0)
    if pending >= thresholds.consumer_lag_critical:
        severity: Severity = "critical"
    elif pending >= thresholds.consumer_lag_warning:
        severity = "warning"
    else:
        severity = "ok"
    results.append(CheckResult(
        name="reporting-sync:consumer_lag",
        severity=severity,
        message=f"{pending} pending message(s)",
        details={
            "pending_messages": pending,
            "ack_pending": consumer.get("ack_pending"),
            "redelivered": consumer.get("redelivered"),
        },
    ))

    return results


def _check_reporting_sync_alerts(client: WIPClient) -> list[CheckResult]:
    """Surface reporting-sync's own active alerts (incl. stall detection)."""
    try:
        data = client.get("reporting-sync", "/alerts")
    except Exception as e:
        return [CheckResult(
            name="reporting-sync:alerts",
            severity="unknown",
            message=f"failed to fetch /alerts: {e}",
        )]

    active = data.get("active_alerts") or []
    if not active:
        return [CheckResult(
            name="reporting-sync:alerts",
            severity="ok",
            message="no active alerts",
        )]

    overall: Severity = "ok"
    summarized = []
    for a in active:
        sev_raw = (a.get("severity") or "warning").lower()
        sev: Severity = "critical" if sev_raw == "critical" else "warning"
        overall = _worse(overall, sev)
        summarized.append({
            "alert_id": a.get("alert_id"),
            "alert_type": a.get("alert_type"),
            "severity": sev_raw,
            "message": a.get("message"),
        })

    return [CheckResult(
        name="reporting-sync:alerts",
        severity=overall,
        message=f"{len(active)} active alert(s)",
        details={"alerts": summarized},
    )]


def _check_ingest_gateway_metrics(
    client: WIPClient,
    thresholds: StatusThresholds,
) -> list[CheckResult]:
    """Hit ingest-gateway /metrics if available."""
    try:
        data = client.get("ingest-gateway", "/metrics")
    except Exception as e:
        return [CheckResult(
            name="ingest-gateway:metrics",
            severity="unknown",
            message=f"failed to fetch /metrics: {e}",
        )]

    total_failed = int(data.get("total_failed", 0) or 0)
    total_processed = int(data.get("total_processed", 0) or 0)
    if total_failed >= thresholds.ingest_failed_warning:
        return [CheckResult(
            name="ingest-gateway:failed",
            severity="warning",
            message=f"{total_failed} failed message(s) since startup",
            details={"total_failed": total_failed, "total_processed": total_processed},
        )]
    return [CheckResult(
        name="ingest-gateway:failed",
        severity="ok",
        message=f"{total_processed} processed, 0 failed",
    )]


def _check_integrity(
    client: WIPClient,
    thresholds: StatusThresholds,
    *,
    template_limit: int,
    document_limit: int,
) -> list[CheckResult]:
    """Hit reporting-sync /health/integrity (aggregates doc-store + template-store)."""
    try:
        data = client.get(
            "reporting-sync",
            "/health/integrity",
            params={
                "template_limit": template_limit,
                "document_limit": document_limit,
            },
        )
    except Exception as e:
        return [CheckResult(
            name="integrity",
            severity="unknown",
            message=f"failed to fetch /health/integrity: {e}",
        )]

    summary = data.get("summary") or {}
    issues = data.get("issues") or []
    drift = (
        int(summary.get("orphaned_terminology_refs", 0) or 0)
        + int(summary.get("orphaned_template_refs", 0) or 0)
        + int(summary.get("orphaned_term_refs", 0) or 0)
    )

    if drift >= thresholds.integrity_drift_critical:
        return [CheckResult(
            name="integrity",
            severity="critical",
            message=f"{drift} orphaned reference(s) detected",
            details={
                "summary": summary,
                "first_issues": issues[:5],
                "services_checked": data.get("services_checked"),
                "services_unavailable": data.get("services_unavailable"),
            },
        )]
    return [CheckResult(
        name="integrity",
        severity="ok",
        message="no orphaned references in scanned window",
        details={"summary": summary, "services_checked": data.get("services_checked")},
    )]
