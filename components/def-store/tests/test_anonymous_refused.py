"""Every route refuses a request that carries no credentials, except the
probes on the explicit public allowlist below.

Provenance: CASE-851 Phase 2b — two services once served every route
anonymously while their wiring tests stayed green. This gate checks the
outcome (a refusal) rather than the wiring, so it catches a missing auth
middleware, a missing route dependency and a mis-allowlisted path alike.
Adding a public route means adding it here, with a reason.
"""

import pytest

from def_store.main import _health_cache, app
from wip_auth.testing import assert_anonymous_refused

PUBLIC = {
    "GET /": "liveness probe (service root)",
    "GET /health": "container health probe",
    "GET /livez": "liveness probe (no I/O): the k8s liveness and startup probes' target",
    "GET /ready": "readiness probe",
    "GET /api/def-store/health": "health probe through the ingress",
    "GET /api/def-store/": "build-provenance probe used by the console",
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
