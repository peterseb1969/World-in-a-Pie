"""A TTL cache for the dependency half of a service's /health response.

Every core service mounts one health handler at two paths with different
jobs. The root ``/health`` is the container-lifecycle probe target
(kubelet/podman), hit every 10s for readiness and every 30s for liveness,
and **only its HTTP status code is ever read**. The api-prefixed
``/api/<service>/health`` is what external callers and the MCP server's
``check_health`` read the *body* of, and it is called occasionally by a
human rather than on a timer.

Re-deriving the dependency fan-out on the probe path meant a document-store
``/health`` opened eight outbound connections eight times a minute, forever,
to produce a body nothing parsed. This caches that work for the probe path
only; the diagnostic path asks for a fresh value and gets one, so an operator
never reads a stale dependency status.

The TTL must exceed the probe interval or the cache cannot hit at all — at a
10s readiness period a 5s TTL expires before every probe and is pure
overhead. The default is deliberately longer than the 30s liveness period.

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
