"""Tests for startup-time retry helpers."""

from __future__ import annotations

import asyncio

import pytest

from wip_auth.startup import retry_async


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
