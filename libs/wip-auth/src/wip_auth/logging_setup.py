"""Process-wide logging for the uvicorn services.

`uvicorn module:app` configures only its own loggers (`uvicorn`,
`uvicorn.error`, `uvicorn.access`). Without this, a service's module loggers
propagate to a root logger that sits at WARNING with no handler: every INFO
line is dropped, and WARNING and above reach stderr through Python's
last-resort handler as a bare message, with no time, logger name or level.

`configure_logging()` gives the root logger one stream handler in the format
every service shares, at the level `WIP_LOG_LEVEL` names (DEBUG, INFO,
WARNING, ERROR, CRITICAL; the service's default when unset or unknown).
httpx and httpcore stay at WARNING or above: httpx logs one INFO line per
request, and the services call each other on almost every write, so at INFO
those lines would bury everything else.

Call it at import time in a service's `main.py`, before the lifespan runs.
A root logger that already has a handler (a test runner's capture, a host
that configured logging itself) keeps its handlers and level.

`configure_logging()` also quiets the health probes (`quiet_health_probes`):
a container healthcheck asks `/ready` or `/health` every few seconds, and
uvicorn's access log wrote a line for each, which buried real requests and
filled the host's journal. A probe that fails is still logged.
"""

from __future__ import annotations

import logging
import os
import re

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_LEVEL_ENV = "WIP_LOG_LEVEL"
# Loggers that write a line per HTTP request at INFO.
_PER_REQUEST_LOGGERS = ("httpx", "httpcore")


def _level_from_env(default: int) -> int:
    name = os.environ.get(LOG_LEVEL_ENV, "").strip().upper()
    if not name:
        return default
    level = logging.getLevelName(name)
    if isinstance(level, int):
        return level
    logging.getLogger(__name__).warning(
        "%s=%r is not a log level; using %s",
        LOG_LEVEL_ENV, name, logging.getLevelName(default),
    )
    return default


# A health probe's path: /health or /ready, bare or under /api/<service>/,
# with any query. Deeper paths (/health/integrity) are real checks and stay.
_PROBE_PATH = re.compile(r"^(?:/api/[^/?]+)?/(?:health|ready)/?(?:\?.*)?$")
_PROBE_METHODS = frozenset({"GET", "HEAD"})


class HealthProbeFilter(logging.Filter):
    """Drops uvicorn access lines of successful health probes.

    uvicorn's access record carries (client, method, path, http version,
    status) as its args; anything else passes untouched."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        _client, method, path, _version, status = args[:5]
        try:
            ok = int(status) < 400
        except (TypeError, ValueError):
            return True
        return not (
            ok and str(method) in _PROBE_METHODS and _PROBE_PATH.match(str(path))
        )


def quiet_health_probes() -> None:
    """Keep successful health probes out of uvicorn's access log
    (idempotent). A probe that answers 4xx or 5xx is still logged."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, HealthProbeFilter) for f in access.filters):
        access.addFilter(HealthProbeFilter())


def configure_logging(default_level: int = logging.INFO) -> None:
    """Configure the root logger for a service process (idempotent)."""
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(handler)
        root.setLevel(_level_from_env(default_level))
    floor = max(root.level, logging.WARNING)
    for name in _PER_REQUEST_LOGGERS:
        logging.getLogger(name).setLevel(floor)
    quiet_health_probes()
