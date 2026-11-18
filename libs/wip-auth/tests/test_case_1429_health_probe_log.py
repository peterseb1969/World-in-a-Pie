"""A successful health probe writes no access line.

Container healthchecks ask /ready or /health every few seconds, and
uvicorn's access log wrote a line for each: in the podman machine that was
over a thousand journal lines per 10 minutes across one install's services,
burying the real requests. A probe that fails is still logged, and so is
every other request, deeper health paths included.
"""

from __future__ import annotations

import logging

import pytest

from wip_auth import configure_logging, quiet_health_probes
from wip_auth.logging_setup import HealthProbeFilter


def _access(method: str, path: str, status: int) -> logging.LogRecord:
    # The shape uvicorn's access logger emits.
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:5000", method, path, "1.1", status), None,
    )


@pytest.mark.parametrize("path", [
    "/ready", "/health", "/health/", "/api/registry/ready", "/api/mcp/health",
    "/health?verbose=1",
])
def test_a_successful_probe_is_dropped(path):
    assert HealthProbeFilter().filter(_access("GET", path, 200)) is False


@pytest.mark.parametrize(("method", "path", "status"), [
    ("GET", "/ready", 503),                    # a failing probe is the line that matters
    ("GET", "/health", 401),
    ("POST", "/health", 200),                  # not a probe
    ("GET", "/health/integrity", 200),         # a real check, not a probe
    ("GET", "/api/registry/entries/ready", 200),
    ("GET", "/api/registry/namespaces", 200),
    ("GET", "/readyz", 200),
])
def test_everything_else_is_logged(method, path, status):
    assert HealthProbeFilter().filter(_access(method, path, status)) is True


def test_a_record_of_another_shape_passes():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 0, "plain", None, None)
    assert HealthProbeFilter().filter(record) is True


def test_configure_logging_installs_the_filter_once():
    access = logging.getLogger("uvicorn.access")
    saved = list(access.filters)
    try:
        for f in [f for f in access.filters if isinstance(f, HealthProbeFilter)]:
            access.removeFilter(f)
        configure_logging()
        quiet_health_probes()
        configure_logging()
        assert sum(isinstance(f, HealthProbeFilter) for f in access.filters) == 1
    finally:
        access.filters[:] = saved


def test_the_access_logger_writes_no_probe_line(caplog):
    access = logging.getLogger("uvicorn.access")
    saved = list(access.filters)
    try:
        quiet_health_probes()
        with caplog.at_level(logging.INFO, logger="uvicorn.access"):
            access.handle(_access("GET", "/ready", 200))
            access.handle(_access("GET", "/ready", 503))
        assert [r.args[2] for r in caplog.records] == ["/ready"]
        assert caplog.records[0].args[4] == 503
    finally:
        access.filters[:] = saved
