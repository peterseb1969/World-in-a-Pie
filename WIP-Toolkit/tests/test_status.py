"""Tests for wip_toolkit.status (CASE-26 — wip-toolkit status aggregator)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from wip_toolkit.status import (
    LIVENESS_SERVICES,
    StatusThresholds,
    collect_status,
)


def _make_client(
    *,
    health: dict[str, tuple[bool, str]] | None = None,
    metrics: dict | None = None,
    alerts: dict | None = None,
    ingest_metrics: dict | None = None,
    integrity: dict | None = None,
):
    """Build a mocked WIPClient with controllable responses."""
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)

    default_health = {s: (True, "healthy") for s in LIVENESS_SERVICES}
    default_health["ingest-gateway"] = (True, "healthy")
    if health:
        default_health.update(health)
    client.check_health.side_effect = lambda service: default_health.get(
        service, (False, "not configured")
    )

    routing = {
        ("reporting-sync", "/metrics"): metrics
        or {
            "started_at": "2026-04-08T14:00:00Z",
            "uptime_seconds": 60.0,
            "nats_connected": True,
            "postgres_connected": True,
            "events_processed": 100,
            "events_failed": 0,
            "consumer_info": {
                "stream_name": "WIP_EVENTS",
                "consumer_name": "reporting-sync",
                "pending_messages": 0,
                "ack_pending": 0,
                "redelivered": 0,
            },
            "errors_by_type": {},
        },
        ("reporting-sync", "/alerts"): alerts or {"active_alerts": []},
        ("ingest-gateway", "/metrics"): ingest_metrics
        or {"total_processed": 0, "total_failed": 0},
        ("reporting-sync", "/health/integrity"): integrity
        or {
            "status": "healthy",
            "summary": {
                "orphaned_terminology_refs": 0,
                "orphaned_template_refs": 0,
                "orphaned_term_refs": 0,
            },
            "issues": [],
            "services_checked": ["template-store", "document-store"],
            "services_unavailable": [],
        },
    }

    def fake_get(service, path, params=None):
        return routing[(service, path)]

    client.get.side_effect = fake_get
    return client


# ----- collect_status -----


def test_status_all_ok():
    client = _make_client()
    report = collect_status(client)
    assert report.overall == "ok"
    assert report.exit_code() == 0
    assert report.services_unreachable == []
    # one check per liveness service + connections + failed_events + lag + alerts + ingest
    assert any(c.name == "reporting-sync:connections" for c in report.checks)
    assert any(c.name == "reporting-sync:failed_events" for c in report.checks)
    assert any(c.name == "reporting-sync:consumer_lag" for c in report.checks)
    assert any(c.name == "reporting-sync:alerts" for c in report.checks)
    assert any(c.name == "ingest-gateway:failed" for c in report.checks)


def test_status_failed_events_warning():
    client = _make_client(metrics={
        "started_at": "2026-04-08T14:00:00Z",
        "uptime_seconds": 60.0,
        "nats_connected": True,
        "postgres_connected": True,
        "events_processed": 1000,
        "events_failed": 5,
        "consumer_info": {
            "stream_name": "X", "consumer_name": "Y",
            "pending_messages": 0, "ack_pending": 0, "redelivered": 0,
        },
        "errors_by_type": {"validation": 5},
    })
    report = collect_status(client)
    assert report.overall == "warning"
    assert report.exit_code() == 1
    failed = next(c for c in report.checks if c.name == "reporting-sync:failed_events")
    assert failed.severity == "warning"
    assert failed.details["events_failed"] == 5
    assert failed.details["errors_by_type"] == {"validation": 5}


def test_status_consumer_lag_warning_then_critical():
    client_warn = _make_client(metrics={
        "nats_connected": True, "postgres_connected": True,
        "events_processed": 0, "events_failed": 0,
        "consumer_info": {"pending_messages": 250, "ack_pending": 0, "redelivered": 0},
    })
    report = collect_status(client_warn)
    assert report.overall == "warning"
    lag = next(c for c in report.checks if c.name == "reporting-sync:consumer_lag")
    assert lag.severity == "warning"

    client_crit = _make_client(metrics={
        "nats_connected": True, "postgres_connected": True,
        "events_processed": 0, "events_failed": 0,
        "consumer_info": {"pending_messages": 5000, "ack_pending": 0, "redelivered": 0},
    })
    report = collect_status(client_crit)
    assert report.overall == "critical"
    assert report.exit_code() == 2


def test_status_disconnected_is_critical():
    client = _make_client(metrics={
        "nats_connected": False, "postgres_connected": True,
        "events_processed": 0, "events_failed": 0,
        "consumer_info": {"pending_messages": 0, "ack_pending": 0, "redelivered": 0},
    })
    report = collect_status(client)
    assert report.overall == "critical"
    conn = next(c for c in report.checks if c.name == "reporting-sync:connections")
    assert conn.severity == "critical"
    assert "NATS" in conn.message


def test_status_required_service_unreachable_is_critical():
    client = _make_client(health={"document-store": (False, "connection refused")})
    report = collect_status(client)
    assert report.overall == "critical"
    assert "document-store" in report.services_unreachable
    liveness = next(
        c for c in report.checks if c.name == "liveness:document-store"
    )
    assert liveness.severity == "critical"


def test_status_optional_service_unreachable_is_unknown_only():
    client = _make_client(health={"ingest-gateway": (False, "connection refused")})
    report = collect_status(client)
    assert "ingest-gateway" in report.services_unreachable
    # No real warning/critical, so the rolled-up overall is "unknown"
    assert report.overall == "unknown"
    assert report.exit_code() == 3
    liveness = next(c for c in report.checks if c.name == "liveness:ingest-gateway")
    assert liveness.severity == "unknown"


def test_status_active_alert_propagates():
    client = _make_client(alerts={
        "active_alerts": [{
            "alert_id": "A-1",
            "alert_type": "processing_stalled",
            "severity": "critical",
            "message": "Processing stalled: no events for 700s",
        }]
    })
    report = collect_status(client)
    assert report.overall == "critical"
    a = next(c for c in report.checks if c.name == "reporting-sync:alerts")
    assert a.severity == "critical"
    assert a.details["alerts"][0]["alert_type"] == "processing_stalled"


def test_status_integrity_drift_is_critical():
    client = _make_client(integrity={
        "summary": {
            "orphaned_terminology_refs": 2,
            "orphaned_template_refs": 0,
            "orphaned_term_refs": 0,
        },
        "issues": [{"type": "orphan", "message": "x"}],
        "services_checked": ["template-store", "document-store"],
        "services_unavailable": [],
    })
    report = collect_status(client, include_integrity=True)
    assert report.overall == "critical"
    integrity = next(c for c in report.checks if c.name == "integrity")
    assert integrity.severity == "critical"
    assert "2 orphaned" in integrity.message


def test_status_integrity_skipped_by_default():
    client = _make_client()
    report = collect_status(client)
    assert all(c.name != "integrity" for c in report.checks)


def test_status_custom_thresholds_respected():
    client = _make_client(metrics={
        "nats_connected": True, "postgres_connected": True,
        "events_processed": 0, "events_failed": 0,
        "consumer_info": {"pending_messages": 50, "ack_pending": 0, "redelivered": 0},
    })
    # With strict warning threshold of 10, 50 pending is a warning
    report = collect_status(
        client,
        StatusThresholds(consumer_lag_warning=10, consumer_lag_critical=100),
    )
    assert report.overall == "warning"


def test_status_to_dict_round_trip():
    client = _make_client()
    report = collect_status(client)
    payload = report.to_dict()
    assert payload["overall"] == "ok"
    assert "checks" in payload
    assert "checked_at" in payload
    assert payload["services_unreachable"] == []


# ----- CLI integration -----


@pytest.fixture
def runner():
    return CliRunner()


def test_cli_status_ok_exit_zero(runner):
    from wip_toolkit.cli import main as cli

    client = _make_client()
    with patch("wip_toolkit.cli.WIPClient", return_value=client):
        result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0


def test_cli_status_critical_exit_two(runner):
    from wip_toolkit.cli import main as cli

    client = _make_client(metrics={
        "nats_connected": True, "postgres_connected": True,
        "events_processed": 0, "events_failed": 0,
        "consumer_info": {"pending_messages": 5000, "ack_pending": 0, "redelivered": 0},
    })
    with patch("wip_toolkit.cli.WIPClient", return_value=client):
        result = runner.invoke(cli, ["status"])
    assert result.exit_code == 2


def test_cli_status_json_output(runner):
    import json

    from wip_toolkit.cli import main as cli

    client = _make_client()
    with patch("wip_toolkit.cli.WIPClient", return_value=client):
        result = runner.invoke(cli, ["status", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["overall"] == "ok"
    assert isinstance(payload["checks"], list)


def test_cli_status_quiet_suppresses_when_ok(runner):
    from wip_toolkit.cli import main as cli

    client = _make_client()
    with patch("wip_toolkit.cli.WIPClient", return_value=client):
        result = runner.invoke(cli, ["status", "--quiet"])
    assert result.exit_code == 0
    # Quiet on OK = no stdout output
    assert result.output == ""


def test_cli_status_quiet_speaks_on_problems(runner):
    from wip_toolkit.cli import main as cli

    client = _make_client(metrics={
        "nats_connected": True, "postgres_connected": True,
        "events_processed": 0, "events_failed": 7,
        "consumer_info": {"pending_messages": 0, "ack_pending": 0, "redelivered": 0},
    })
    with patch("wip_toolkit.cli.WIPClient", return_value=client):
        result = runner.invoke(cli, ["status", "--quiet", "--json"])
    assert result.exit_code == 1
    assert result.output  # JSON printed because there's a warning
