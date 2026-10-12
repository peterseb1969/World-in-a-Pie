"""Every route refuses a request that carries no credentials, except the
probes on the explicit public allowlist below.

Provenance: CASE-855 / Phase 2b. This service once served every route
without credentials; this gate checks the outcome (a refusal) rather
than the wiring.
"""

from ingest_gateway.main import app
from wip_auth.testing import assert_anonymous_refused

PUBLIC = {
    "GET /api/ingest-gateway/health": "container and ingress health probe",
    "GET /api/ingest-gateway/": "service root / build-provenance probe",
}


def test_every_route_refuses_anonymous_requests():
    assert_anonymous_refused(app, public=PUBLIC)
