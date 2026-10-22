"""The startup metadata backfill retries until complete (CASE-862, slice S1).

reporting-sync backfills terminologies, terms, term relations and template
metadata at startup. It used to wait for def-store's /health only, then run
every step once: template-store was never waited for, and a listing that
failed (refused, or 500 from a source whose /health said 200) was reported as
`synced: 0` and never retried — the reporting tables stayed empty until each
entity's next write. Each step now waits on its own source and retries with
capped backoff until its batch sync reports a complete listing.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import httpx
import pytest

from reporting_sync import main
from reporting_sync.batch_sync import BatchSyncService
from reporting_sync.config import settings

DEF = settings.def_store_url
TPL = settings.template_store_url


@pytest.fixture
def delays(monkeypatch):
    """Records backoff delays instead of sleeping."""
    seen: list[float] = []

    async def no_sleep(delay):
        seen.append(delay)
        await asyncio.sleep(0)  # yield, as a real sleep does

    monkeypatch.setattr(main, "_backfill_sleep", no_sleep)
    return seen


def _health(monkeypatch, down: dict[str, int]):
    """Each source answers unhealthy for its first `down[url]` checks."""
    left = dict(down)

    async def healthy(url):
        if left.get(url, 0) > 0:
            left[url] -= 1
            return False
        return True

    monkeypatch.setattr(main, "_source_healthy", healthy)


def _sync(name: str, events: list[str], incomplete_first: int = 0, raise_first: int = 0):
    """A fake batch sync: fails (incomplete or raising) for its first calls."""
    calls = {"n": 0}

    async def sync():
        calls["n"] += 1
        if calls["n"] <= raise_first:
            raise RuntimeError("boom")
        if calls["n"] <= raise_first + incomplete_first:
            return {"synced": 0, "failed": 0, "total": 0, "complete": False}
        events.append(name)
        return {"synced": 1, "failed": 0, "total": 1, "complete": True}

    sync.calls = calls  # type: ignore[attr-defined]
    return sync


def _service(**syncs) -> MagicMock:
    service = MagicMock()
    for name, fn in syncs.items():
        setattr(service, f"batch_sync_{name}", fn)
    return service


@pytest.mark.asyncio
async def test_template_store_late_is_waited_for(monkeypatch, delays):
    """Repro (a): template-store not up when the backfill starts."""
    events: list[str] = []
    _health(monkeypatch, {TPL: 2})
    service = _service(
        terminologies=_sync("terminologies", events), terms=_sync("terms", events),
        term_relations=_sync("term_relations", events), templates=_sync("templates", events),
    )
    await main._initial_metadata_sync(service)
    assert sorted(events) == ["templates", "term_relations", "terminologies", "terms"]
    assert service.batch_sync_templates.calls["n"] == 1


@pytest.mark.asyncio
async def test_a_healthy_source_whose_listing_fails_is_retried(monkeypatch, delays):
    """Repro (b): /health 200, but the listing fails for the first 3 calls."""
    events: list[str] = []
    _health(monkeypatch, {})
    templates = _sync("templates", events, incomplete_first=3)
    service = _service(
        terminologies=_sync("terminologies", events), terms=_sync("terms", events),
        term_relations=_sync("term_relations", events), templates=templates,
    )
    await main._initial_metadata_sync(service)
    assert templates.calls["n"] == 4
    assert "templates" in events


@pytest.mark.asyncio
async def test_a_raising_sync_counts_as_incomplete(monkeypatch, delays):
    events: list[str] = []
    _health(monkeypatch, {})
    terminologies = _sync("terminologies", events, raise_first=2)
    service = _service(
        terminologies=terminologies, terms=_sync("terms", events),
        term_relations=_sync("term_relations", events), templates=_sync("templates", events),
    )
    await main._initial_metadata_sync(service)
    assert terminologies.calls["n"] == 3
    assert events.index("terminologies") < events.index("terms") < events.index("term_relations")


@pytest.mark.asyncio
async def test_def_store_down_does_not_hold_back_templates(monkeypatch, delays):
    events: list[str] = []
    _health(monkeypatch, {DEF: 5})
    service = _service(
        terminologies=_sync("terminologies", events), terms=_sync("terms", events),
        term_relations=_sync("term_relations", events), templates=_sync("templates", events),
    )
    await main._initial_metadata_sync(service)
    assert events[0] == "templates"


@pytest.mark.asyncio
async def test_backoff_doubles_and_caps_at_a_minute(monkeypatch, delays):
    events: list[str] = []
    _health(monkeypatch, {TPL: 7})
    service = _service(
        terminologies=_sync("terminologies", events), terms=_sync("terms", events),
        term_relations=_sync("term_relations", events), templates=_sync("templates", events),
    )
    await main._initial_metadata_sync(service)
    assert delays == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 60.0]


# ── the batch syncs report whether their listing completed ────────────────


@pytest.mark.asyncio
async def test_a_failed_listing_is_incomplete_not_empty(httpx_mock):
    httpx_mock.add_response(url=f"{DEF}/api/def-store/terminologies?page=1&page_size=100", status_code=500)
    result = await BatchSyncService(MagicMock()).batch_sync_terminologies()
    assert result == {"synced": 0, "failed": 0, "total": 0, "complete": False}


@pytest.mark.asyncio
async def test_an_unreachable_source_is_incomplete(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    result = await BatchSyncService(MagicMock()).batch_sync_templates()
    assert result["complete"] is False


@pytest.mark.asyncio
async def test_an_empty_namespace_is_complete(httpx_mock):
    httpx_mock.add_response(
        url=f"{TPL}/api/template-store/templates?page=1&page_size=100&latest_only=true",
        json={"items": [], "pages": 0},
    )
    result = await BatchSyncService(MagicMock()).batch_sync_templates()
    assert result == {"synced": 0, "failed": 0, "total": 0, "complete": True}
