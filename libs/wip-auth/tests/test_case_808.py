"""HealthCache behaviour (CASE-808).

The probe path calls /health 8 times a minute and reads only the HTTP status
code; the api-prefixed path reads the body and must never be stale. These
tests pin that split, plus the property that made the original 5s proposal
useless: a TTL shorter than the probe interval can never hit.
"""

import asyncio

import pytest

from wip_auth.health_cache import DEFAULT_TTL_SECONDS, HealthCache


class _Counter:
    """Records how many times the expensive fan-out actually ran."""

    def __init__(self) -> None:
        self.calls = 0

    async def produce(self) -> dict[str, str]:
        self.calls += 1
        return {"database": "connected", "call": str(self.calls)}


@pytest.mark.asyncio
async def test_repeat_calls_within_ttl_do_not_re_probe():
    c = _Counter()
    cache: HealthCache[dict[str, str]] = HealthCache(ttl=60.0)

    first = await cache.get(c.produce)
    for _ in range(10):
        again = await cache.get(c.produce)
        assert again == first

    # This is the whole point: eight probes a minute, one fan-out.
    assert c.calls == 1


@pytest.mark.asyncio
async def test_expiry_re_probes():
    c = _Counter()
    cache: HealthCache[dict[str, str]] = HealthCache(ttl=0.05)

    await cache.get(c.produce)
    await asyncio.sleep(0.08)
    await cache.get(c.produce)

    assert c.calls == 2


@pytest.mark.asyncio
async def test_force_bypasses_the_cache_for_the_diagnostic_path():
    c = _Counter()
    cache: HealthCache[dict[str, str]] = HealthCache(ttl=60.0)

    await cache.get(c.produce)
    fresh = await cache.get(c.produce, force=True)

    assert c.calls == 2
    assert fresh["call"] == "2"
    # A forced read also re-stamps, so the probe path serves the new value.
    assert (await cache.get(c.produce))["call"] == "2"
    assert c.calls == 2


@pytest.mark.asyncio
async def test_concurrent_probes_collapse_to_one_fan_out():
    """A burst arriving on an expired entry must not start N fan-outs."""

    class _Slow(_Counter):
        async def produce(self) -> dict[str, str]:
            self.calls += 1
            await asyncio.sleep(0.05)
            return {"database": "connected", "call": str(self.calls)}

    c = _Slow()
    cache: HealthCache[dict[str, str]] = HealthCache(ttl=60.0)

    results = await asyncio.gather(*(cache.get(c.produce) for _ in range(8)))

    assert c.calls == 1
    assert all(r == results[0] for r in results)


@pytest.mark.asyncio
async def test_invalidate_forces_the_next_read_to_re_probe():
    c = _Counter()
    cache: HealthCache[dict[str, str]] = HealthCache(ttl=60.0)

    await cache.get(c.produce)
    cache.invalidate()
    await cache.get(c.produce)

    assert c.calls == 2


def test_default_ttl_exceeds_the_probe_intervals_it_exists_to_absorb():
    # Readiness fires every 10s and liveness every 30s. A TTL at or below the
    # readiness period expires before every probe and the cache never hits —
    # the ~5s originally proposed for this case would have been pure overhead.
    READINESS_PERIOD = 10
    LIVENESS_PERIOD = 30
    assert DEFAULT_TTL_SECONDS > READINESS_PERIOD
    assert DEFAULT_TTL_SECONDS >= LIVENESS_PERIOD
