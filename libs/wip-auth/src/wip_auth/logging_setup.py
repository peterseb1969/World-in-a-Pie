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
"""

from __future__ import annotations

import logging
import os

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
