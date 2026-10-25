"""System-terminology bootstrap never gives up on a late Registry.

Provenance: CASE-862 S3 — the bootstrap waited 120 s for Registry and then
stopped for good, so system terminologies stayed missing until def-store was
restarted. It now keeps trying until Registry answers and the bootstrap runs
without errors.
"""

import pytest

import def_store.main as main


class _FakeRegistry:
    def __init__(self, healthy_from_call: int) -> None:
        self.calls = 0
        self.healthy_from_call = healthy_from_call

    async def health_check(self) -> bool:
        self.calls += 1
        return self.calls >= self.healthy_from_call


def _result(errors: list[str]) -> dict:
    return {"terminologies_created": 0, "terminologies_existed": 3,
            "terms_created": 0, "terms_existed": 9, "errors": errors}


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(main, "_bootstrap_sleep", fake_sleep)
    return sleeps


@pytest.mark.asyncio
async def test_waits_past_the_old_deadline_for_registry(monkeypatch, no_sleep):
    """Registry healthy only on the 40th check — far past the old 120 s budget."""
    registry = _FakeRegistry(healthy_from_call=40)
    ensured = []

    async def ensure():
        ensured.append(1)
        return _result([])

    monkeypatch.setattr(main, "get_registry_client", lambda: registry)
    monkeypatch.setattr(main, "ensure_system_terminologies", ensure)

    await main._bootstrap_system_terminologies()

    assert registry.calls == 40
    assert ensured == [1]  # bootstrapped exactly once, after Registry came up
    assert len(no_sleep) == 39


@pytest.mark.asyncio
async def test_an_attempt_with_errors_is_retried(monkeypatch, no_sleep):
    results = iter([_result(["_TIME_UNITS: registry refused"]), _result([])])
    calls = []

    async def ensure():
        calls.append(1)
        return next(results)

    monkeypatch.setattr(main, "get_registry_client", lambda: _FakeRegistry(healthy_from_call=1))
    monkeypatch.setattr(main, "ensure_system_terminologies", ensure)

    await main._bootstrap_system_terminologies()

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_raising_attempt_is_retried(monkeypatch, no_sleep):
    calls = []

    async def ensure():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("registry went away mid-bootstrap")
        return _result([])

    monkeypatch.setattr(main, "get_registry_client", lambda: _FakeRegistry(healthy_from_call=1))
    monkeypatch.setattr(main, "ensure_system_terminologies", ensure)

    await main._bootstrap_system_terminologies()

    assert len(calls) == 2
