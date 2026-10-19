"""Every route refuses a request that carries no credentials, except the
probes on the explicit public allowlist below.

Provenance: CASE-851 Phase 2b. This service once served every route
without credentials while its wiring tests stayed green; this gate
checks the outcome (a refusal) rather than the wiring.
"""

from reporting_sync.main import app
from wip_auth.testing import assert_anonymous_refused

PUBLIC = {
    "GET /health": "container health probe",
    "GET /livez": "liveness probe (no I/O): the k8s liveness and startup probes' target",
    "GET /api/reporting-sync/health": "health probe through the ingress",
    "GET /api/reporting-sync/": "service root / build-provenance probe",
}


def test_every_route_refuses_anonymous_requests():
    assert_anonymous_refused(app, public=PUBLIC)
