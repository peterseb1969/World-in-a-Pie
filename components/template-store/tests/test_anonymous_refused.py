"""Every route refuses a request that carries no credentials, except the
probes on the explicit public allowlist below.

Provenance: CASE-851 Phase 2b — two services once served every route
anonymously while their wiring tests stayed green. This gate checks the
outcome (a refusal) rather than the wiring, so it catches a missing auth
middleware, a missing route dependency and a mis-allowlisted path alike.
Adding a public route means adding it here, with a reason.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from template_store.main import _health_cache, app
from wip_auth.testing import assert_anonymous_refused

PUBLIC = {
    "GET /": "liveness probe (service root)",
    "GET /health": "container health probe",
    "GET /ready": "readiness probe",
    "GET /api/template-store/health": "health probe through the ingress",
    "GET /api/template-store/": "build-provenance probe used by the console",
}


@pytest.fixture(autouse=True)
def _drop_cached_health():
    """The walker probes /health without running the lifespan, so that
    probe sees no database and its 'unhealthy' result is cached
    module-wide. Drop it so later health tests run a real probe."""
    yield
    _health_cache.invalidate()


def test_every_route_refuses_anonymous_requests():
    assert_anonymous_refused(app, public=PUBLIC)


@pytest.mark.asyncio
async def test_integrity_check_is_operator_only():
    """/health/integrity scans every namespace with the service's own
    access: an authenticated caller without platform privileges is refused,
    the install service key passes the gate (the handler may then fail on
    missing test infrastructure — only the gate is under test here)."""
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        scoped = await client.get(
            "/health/integrity", headers={"X-API-Key": "test_scoped_key"}
        )
        admin = await client.get(
            "/health/integrity", headers={"X-API-Key": os.environ["API_KEY"]}
        )
    assert scoped.status_code == 403
    assert admin.status_code not in (401, 403)
