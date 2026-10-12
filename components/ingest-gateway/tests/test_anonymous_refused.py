"""Every route refuses a request that carries no credentials, except the
probes on the explicit public allowlist below.

Provenance: CASE-855 / Phase 2b. This service does not authenticate yet,
so the gate is recorded as a strict expected failure: it documents the
open gap mechanically, and the moment authentication lands the marker
turns into a failure ("XPASS(strict)") that forces its own removal.
"""

import pytest

from ingest_gateway.main import app
from wip_auth.testing import assert_anonymous_refused

PUBLIC = {
    "GET /api/ingest-gateway/health": "container and ingress health probe",
    "GET /api/ingest-gateway/": "service root / build-provenance probe",
}


@pytest.mark.xfail(
    strict=True,
    reason="CASE-855: ingest-gateway serves every route without "
    "authentication (Phase 2b C6 closes it)",
)
def test_every_route_refuses_anonymous_requests():
    assert_anonymous_refused(app, public=PUBLIC)
