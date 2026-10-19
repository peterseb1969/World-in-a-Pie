"""Every route refuses a request that carries no credentials, except the
probes on the explicit public allowlist below.

Provenance: CASE-851 Phase 2b — two services once served every route
anonymously while their wiring tests stayed green. This gate checks the
outcome (a refusal) rather than the wiring, so it catches a missing auth
middleware, a missing route dependency and a mis-allowlisted path alike.
Adding a public route means adding it here, with a reason.
"""

from registry.main import app
from wip_auth.testing import assert_anonymous_refused

PUBLIC = {
    "GET /": "liveness probe (service root)",
    "GET /health": "container health probe",
    "GET /livez": "liveness probe (no I/O): the k8s liveness and startup probes' target",
    "GET /ready": "readiness probe",
    "GET /api/registry/health": "health probe through the ingress",
    "GET /api/registry/": "build-provenance probe used by the console",
}


def test_every_route_refuses_anonymous_requests():
    assert_anonymous_refused(app, public=PUBLIC)
