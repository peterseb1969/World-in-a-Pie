"""CASE-971 — restore planning does not stall the event loop.

A merge planned the whole archive synchronously on the event loop (the
plan-input pass, then a second full pass for the referenced ids), and the
fresh restore's planner did the same per entity type. The document-store's
single worker answered nothing meanwhile, /livez included. The passes now
run on a worker thread, and the engine's progress events are handed back to
the loop from there (its sink, an asyncio.Queue, is not thread-safe).

Each test slows one pass down with a BLOCKING sleep and runs a ticker
coroutine beside the restore: while the pass runs, the ticker must keep
ticking. On the loop, the sleep would freeze it.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from document_store.services import backup_engine, remap_restore
from tests.test_case_959_merge_streams import (  # noqa: F401 — the fixture
    _archive_docs,
    _fresh,
    _fresh_archive_docs,
    _merge,
    mongo,
)

STALL = 0.4  # seconds each slowed pass blocks for
TICK = 0.02


class _Window:
    """When the slowed pass ran, and when the ticker ticked."""

    def __init__(self) -> None:
        self.spans: list[tuple[float, float]] = []
        self.ticks: list[float] = []

    def slowed(self, fn):
        def wrapper(*args, **kwargs):
            start = time.monotonic()
            time.sleep(STALL)
            try:
                return fn(*args, **kwargs)
            finally:
                self.spans.append((start, time.monotonic()))
        return wrapper

    async def ticker(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            self.ticks.append(time.monotonic())
            await asyncio.sleep(TICK)

    def ticks_inside_slowed_passes(self) -> int:
        return sum(1 for t in self.ticks for a, b in self.spans if a < t < b)


async def _beside(window: _Window, work) -> object:
    stop = asyncio.Event()
    ticker = asyncio.create_task(window.ticker(stop))
    try:
        return await work
    finally:
        stop.set()
        await ticker


@pytest.mark.asyncio
async def test_merge_planning_leaves_the_loop_free(mongo) -> None:  # noqa: F811
    window = _Window()
    real = backup_engine.compact_heads
    with patch.object(backup_engine, "compact_heads", window.slowed(real)):
        rows, _ = await _beside(window, _merge(mongo, _archive_docs(), on_clash="overwrite"))

    assert window.spans, "the planning pass did not run"
    # Ticking every 20 ms for 400 ms: well over ten ticks inside the pass.
    assert window.ticks_inside_slowed_passes() >= 5
    assert {r["document_id"] for r in rows} >= {"D1", "D2", "D3", "D4"}


@pytest.mark.asyncio
async def test_a_warning_from_the_planning_thread_still_arrives(mongo) -> None:  # noqa: F811
    # An unsorted archive makes the planner emit a warning — now from its
    # worker thread. The event must reach the progress sink all the same.
    docs = list(reversed(_archive_docs()))
    _, events = await _merge(mongo, docs, on_clash="overwrite")
    assert any("not sorted by document_id" in (e.message or "") for e in events)


@pytest.mark.asyncio
async def test_fresh_restore_planning_leaves_the_loop_free(mongo) -> None:  # noqa: F811
    window = _Window()
    real = remap_restore.RemapRestore._rewrite_type
    with patch.object(remap_restore.RemapRestore, "_rewrite_type", window.slowed(real)):
        rows, _ = await _beside(window, _fresh(mongo, _fresh_archive_docs()))

    assert window.spans, "the rewrite pass did not run"
    assert window.ticks_inside_slowed_passes() >= 5
    assert len(rows) == len(_fresh_archive_docs())
