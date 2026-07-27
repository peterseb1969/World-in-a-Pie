"""The /health handler's caching and concurrency wiring (CASE-808).

Guards the wiring, not the cache primitive — HealthCache itself is covered in
libs/wip-auth/tests/test_case_808.py. What can silently revert here is the
handler being changed back to re-probing on every call, or the api-prefixed
route being re-pointed at the cached function.

The response contract is asserted explicitly: this case exists precisely
BECAUSE it must not change what /health returns. CASE-804 proposed changing
the probe contract and was closed for it; anything that alters the body shape
or the always-200 behaviour here has reintroduced that risk.
"""

from unittest.mock import AsyncMock, patch

import pytest

from document_store import main as ds_main

EXPECTED_KEYS = {
    "status",
    "database",
    "registry",
    "template_store",
    "def_store",
    "nats",
    "file_storage",
}


@pytest.fixture
def stub_dependencies():
    """Make every dependency answer instantly, and count the fan-outs."""
    counter = {"calls": 0}

    real_probe = ds_main._probe_dependencies

    async def counting_probe():
        counter["calls"] += 1
        return await real_probe()

    healthy = AsyncMock(return_value=True)
    with (
        patch.object(ds_main, "_mongo_status", AsyncMock(return_value="connected")),
        patch.object(ds_main, "_file_storage_status", AsyncMock(return_value="disabled")),
        patch.object(ds_main, "get_registry_client", return_value=AsyncMock(health_check=healthy)),
        patch.object(ds_main, "get_template_store_client", return_value=AsyncMock(health_check=healthy)),
        patch.object(ds_main, "get_def_store_client", return_value=AsyncMock(health_check=healthy)),
        patch.object(ds_main, "nats_health_check", AsyncMock(return_value=True)),
        patch.object(ds_main, "_probe_dependencies", counting_probe),
    ):
        ds_main._health_cache.invalidate()
        yield counter
    ds_main._health_cache.invalidate()


@pytest.mark.asyncio
async def test_probe_path_reuses_the_cached_fan_out(stub_dependencies):
    # The kubelet hits this 8x/min and reads only the status code.
    for _ in range(8):
        body = await ds_main.health_check()
        assert body["status"] == "healthy"

    assert stub_dependencies["calls"] == 1


@pytest.mark.asyncio
async def test_diagnostic_path_always_re_probes(stub_dependencies):
    await ds_main.health_check()          # populate via the probe path
    await ds_main.health_check_fresh()    # a human reading the body
    await ds_main.health_check_fresh()

    assert stub_dependencies["calls"] == 3


@pytest.mark.asyncio
async def test_response_contract_is_unchanged(stub_dependencies):
    body = await ds_main.health_check()
    assert set(body) == EXPECTED_KEYS
    # Every value is a plain string, as before — nothing nested or restructured.
    assert all(isinstance(v, str) for v in body.values())


@pytest.mark.asyncio
async def test_a_dead_dependency_still_reports_healthy_and_does_not_raise(
    stub_dependencies,
):
    """The always-200 behaviour is load-bearing for parallel start.

    Services report healthy as soon as their ASGI app serves, independent of
    dependency state; `_depends_on_block` emits no depends_on precisely
    because startup retry handles ordering. A /health that raised or reported
    unhealthy on a disconnected peer would partially reserialize cold boot.
    """
    with patch.object(
        ds_main, "get_registry_client", return_value=AsyncMock(
            health_check=AsyncMock(return_value=False)
        )
    ):
        ds_main._health_cache.invalidate()
        body = await ds_main.health_check()

    assert body["registry"] == "disconnected"
    assert body["status"] == "healthy"  # mongo is up; the peer is not our health


@pytest.mark.asyncio
async def test_the_two_routes_are_wired_to_different_functions():
    """Root -> cached, api-prefixed -> fresh. Re-pointing either is the revert."""
    routes = {
        r.path: r.endpoint
        for r in ds_main.app.routes
        if getattr(r, "path", None) in ("/health", "/api/document-store/health")
    }
    assert routes["/health"] is ds_main.health_check
    assert routes["/api/document-store/health"] is ds_main.health_check_fresh
