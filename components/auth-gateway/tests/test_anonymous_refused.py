"""Every route refuses a request that carries no credentials, except the
entries on the explicit public allowlist below.

Provenance: CASE-851 Phase 2b. The auth-gateway IS the authenticator: it
checks the session cookie inside its handlers, so the walker sees the
outcome directly — /auth/verify and /auth/userinfo must answer 401
without a session. Its OIDC protocol endpoints are public by design (a
user without a session has to be able to start and finish a login).
"""

from auth_gateway.main import app
from wip_auth.testing import assert_anonymous_refused

PUBLIC = {
    "GET /health": "container health probe",
    "GET /auth/login": "OIDC protocol endpoint: starts the login flow",
    "GET /auth/callback": "OIDC protocol endpoint: completes the login flow",
    "GET /auth/logout": "OIDC protocol endpoint: ends the session",
}


def test_every_route_refuses_anonymous_requests():
    assert_anonymous_refused(app, public=PUBLIC)
