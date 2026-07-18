"""Startup-time dependency retry helpers.

Python services in WIP depend on MongoDB (via beanie/motor) and on each
other via HTTP (e.g., def-store calls registry during terminology
bootstrap). On k8s, pods come up concurrently — a dependency may not be
ready when a dependent service starts. Without retry, the service exits
and relies on k8s's restart-loop to eventually succeed, which produces
visible pod restarts on every fresh deploy, node drain, reschedule, and
rolling update.

This module provides small helpers to retry startup-time connection /
HTTP calls with a fixed polling interval (default 5s). Fixed interval
instead of exponential backoff is a deliberate choice: the underlying
operations (TCP connect, HTTP GET) are microsecond-cheap locally, so
backoff's network-friendliness benefit doesn't apply. Fixed polling
bounds the worst-case wait after the dependency recovers.

Scope: startup only. Runtime MongoDB flap is handled by motor/pymongo's
internal topology watcher. Runtime HTTP-call failures surface as
HTTP 500 to the caller with no retry — tracked as a follow-up.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

_DEFAULT_MAX_WAIT_SECONDS = 120
_DEFAULT_POLL_INTERVAL_SECONDS = 5.0


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    retry_on: tuple[type[BaseException], ...],
    description: str,
    max_wait_seconds: int = _DEFAULT_MAX_WAIT_SECONDS,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
) -> T:
    """Retry an async operation with fixed-interval polling.

    Calls `operation()` repeatedly until it returns or the total wait
    exceeds `max_wait_seconds`. Only retries on exceptions in `retry_on`;
    anything else propagates immediately.

    Args:
        operation: Zero-arg async callable. Called fresh each attempt.
        retry_on: Exception types that indicate "dependency not ready,
            keep polling."
        description: Human-readable description for log messages.
            Example: "MongoDB init (wip_registry)".
        max_wait_seconds: Total budget across all attempts. Default 120.
        poll_interval_seconds: Fixed delay between attempts. Default 5.

    Returns:
        Whatever `operation()` returns on success.

    Raises:
        The last caught exception (from `retry_on`) if the deadline
        elapses. Any non-`retry_on` exception propagates immediately.
    """
    deadline = time.monotonic() + max_wait_seconds
    last_exc: BaseException | None = None
    attempt = 0

    while True:
        attempt += 1
        try:
            return await operation()
        except retry_on as exc:
            last_exc = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sleep_for = min(poll_interval_seconds, remaining)
            print(
                f"[startup-retry] {description}: attempt {attempt} failed "
                f"({type(exc).__name__}: {exc}); "
                f"retrying in {sleep_for:.1f}s ({remaining:.0f}s remaining)"
            )
            await asyncio.sleep(sleep_for)

    assert last_exc is not None
    raise TimeoutError(
        f"{description} did not succeed within {max_wait_seconds}s: {last_exc}"
    ) from last_exc


async def init_beanie_with_retry(
    *,
    database,  # motor database  # type: ignore[no-untyped-def]
    document_models: list,  # type: ignore[type-arg]
    description: str = "MongoDB init",
    max_wait_seconds: int = _DEFAULT_MAX_WAIT_SECONDS,
    poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
) -> None:
    """Wrap `beanie.init_beanie` with startup-time retry.

    Retries on `pymongo.errors.ServerSelectionTimeoutError` and
    `ConnectionFailure` — the two errors thrown when MongoDB isn't yet
    accepting connections. Anything else (auth failure, invalid document
    model) surfaces immediately.
    """
    from beanie import init_beanie  # lazy import — only services use this
    from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

    async def _init() -> None:
        await init_beanie(database=database, document_models=document_models)

    await retry_async(
        _init,
        retry_on=(ServerSelectionTimeoutError, ConnectionFailure),
        description=description,
        max_wait_seconds=max_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
