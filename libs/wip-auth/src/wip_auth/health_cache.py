"""A TTL cache for the dependency half of a service's /health response.

Every core service mounts one health handler at two paths. The deployer's
healthchecks and k8s probes target neither: they probe ``/ready`` and
``/livez`` (``livez`` does no I/O). The root ``/health`` is what polls it on
a timer: the image's own HEALTHCHECK (``curl -f`` every 30s, reading only
the status code) and peer services checking a dependency (document-store
and reporting-sync check the stores' root ``/health``). The api-prefixed
``/api/<service>/health`` is the diagnostic route, read for its body (the
MCP server's status tool, a human).

Re-deriving the dependency fan-out on every poll costs a service one
outbound connection per dependency per call (eight for document-store,
counting the chains its peers run in turn). This caches that work for the
root path only; the diagnostic path asks for a fresh value and gets one, so
an operator never reads a stale dependency status.

The TTL bounds how stale a polling client's view can be: an entry is fresh
for strictly less than ``ttl`` seconds after it was produced, so a client
polling at exactly the TTL period misses every time and one polling faster
shares the fan-out.

Deliberately not a decorator and deliberately per-instance: the state is one
tuple, and a module-level singleton would leak values between the tests that
exercise different dependency sets.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

T = TypeVar("T")

DEFAULT_TTL_SECONDS = 30.0


class HealthCache(Generic[T]):
    """Holds the most recent dependency-probe result for up to ``ttl``.

    Not thread-safe by design — it guards an asyncio event loop, not threads.
    Concurrent refreshes are collapsed with a lock so a burst of probes
    arriving on an expired entry produces one fan-out, not several.
    """

    def __init__(self, ttl: float = DEFAULT_TTL_SECONDS) -> None:
        self.ttl = ttl
        self._value: T | None = None
        self._stamped_at: float = 0.0
        self._lock = asyncio.Lock()

    def _fresh_enough(self, now: float) -> bool:
        return self._value is not None and (now - self._stamped_at) < self.ttl

    async def get(
        self, produce: Callable[[], Awaitable[T]], *, force: bool = False
    ) -> T:
        """Return a cached value, or produce one.

        ``force=True`` always re-derives and re-stamps — the diagnostic path
        passes it so a human never reads a stale dependency status, while
        still refreshing what the probe path will serve next.
        """
        now = time.monotonic()
        if not force and self._fresh_enough(now):
            return self._value  # type: ignore[return-value]

        async with self._lock:
            # Re-check under the lock: a probe that queued behind a refresh
            # should use its result rather than start a second one. `force`
            # skips this, since its caller asked for a genuinely fresh read.
            now = time.monotonic()
            if not force and self._fresh_enough(now):
                return self._value  # type: ignore[return-value]
            value = await produce()
            self._value = value
            self._stamped_at = time.monotonic()
            return value

    def invalidate(self) -> None:
        """Drop the cached value — next get() re-derives."""
        self._value = None
        self._stamped_at = 0.0


__all__ = ["DEFAULT_TTL_SECONDS", "HealthCache"]
