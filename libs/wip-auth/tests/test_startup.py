"""Tests for startup-time retry helpers."""

from __future__ import annotations

import asyncio

import pytest

from wip_auth.startup import retry_async, retry_until_done


class CustomError(Exception):
    pass


class OtherError(Exception):
    pass


@pytest.mark.asyncio
async def test_returns_on_first_success() -> None:
    async def op() -> str:
        return "ok"

    result = await retry_async(
        op, retry_on=(CustomError,), description="test",
        max_wait_seconds=2, poll_interval_seconds=0.01,
    )
    assert result == "ok"


@pytest.mark.asyncio
async def test_retries_then_succeeds() -> None:
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise CustomError("not yet")
        return "ok"

    result = await retry_async(
        op, retry_on=(CustomError,), description="test",
        max_wait_seconds=5, poll_interval_seconds=0.01,
    )
    assert result == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_raises_timeout_after_deadline() -> None:
    async def op() -> str:
        raise CustomError("never ready")

    with pytest.raises(TimeoutError, match="did not succeed within"):
        await retry_async(
            op, retry_on=(CustomError,), description="test",
            max_wait_seconds=1, poll_interval_seconds=0.01,
        )


@pytest.mark.asyncio
async def test_non_retry_exception_propagates_immediately() -> None:
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        raise OtherError("fatal")

    with pytest.raises(OtherError, match="fatal"):
        await retry_async(
            op, retry_on=(CustomError,), description="test",
            max_wait_seconds=5, poll_interval_seconds=0.01,
        )
    assert calls["n"] == 1  # did not retry


@pytest.mark.asyncio
async def test_poll_interval_shortens_near_deadline() -> None:
    """Final sleep must not push past the deadline — sleep_for clamps to
    remaining time. This keeps the test from hanging past max_wait."""
    async def op() -> str:
        raise CustomError("always fails")

    start = asyncio.get_event_loop().time()
    with pytest.raises(TimeoutError):
        await retry_async(
            op, retry_on=(CustomError,), description="test",
            max_wait_seconds=1, poll_interval_seconds=10.0,
        )
    elapsed = asyncio.get_event_loop().time() - start
    # Should finish right around max_wait, not 10+ seconds
    assert elapsed < 3.0


# -- retry_until_done -----------------------------------------------------------


class _Clock:
    """Records requested sleeps instead of sleeping."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


@pytest.mark.asyncio
async def test_until_done_returns_when_done() -> None:
    clock = _Clock()
    await retry_until_done(_async_return(True), description="t", sleep=clock.sleep)
    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_until_done_keeps_going_past_any_deadline() -> None:
    """No deadline: ten failures, then success, and the task is still trying."""
    clock = _Clock()
    outcomes = iter([False] * 10 + [True])

    async def attempt() -> bool:
        return next(outcomes)

    await retry_until_done(attempt, description="t", sleep=clock.sleep)
    assert len(clock.sleeps) == 10
    assert clock.sleeps[:5] == [5.0, 10.0, 20.0, 40.0, 60.0]
    assert set(clock.sleeps[4:]) == {60.0}  # capped


@pytest.mark.asyncio
async def test_until_done_treats_an_exception_as_not_done() -> None:
    clock = _Clock()
    calls = 0

    async def attempt() -> bool:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("dependency down")
        return True

    await retry_until_done(attempt, description="t", sleep=clock.sleep)
    assert calls == 3


@pytest.mark.asyncio
async def test_until_done_stops_on_cancellation() -> None:
    async def attempt() -> bool:
        return False

    task = asyncio.create_task(
        retry_until_done(attempt, description="t", first_delay_seconds=0.01, max_delay_seconds=0.01)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def _async_return(value: bool):
    async def attempt() -> bool:
        return value
    return attempt
