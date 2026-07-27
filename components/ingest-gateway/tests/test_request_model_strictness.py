"""Every request-body model on this service rejects unknown keys.

A lenient request model silently drops unknown keys — the caller believes
an option took effect while the server never saw it. The platform rule is
extra='forbid' on every model that parses caller input; this gate walks
the app's routes so a new request model fails here no matter which file
it is born in. Retire a field with a tombstone (keep it, reject it loudly
with its story) — never by loosening the model.
"""

from ingest_gateway.main import app
from wip_auth.testing import assert_strict_request_models


def test_request_models_forbid_unknown_keys():
    # The gateway's HTTP surface is read-only (GET health/status/metrics);
    # ingest payloads arrive over NATS, where the strict IngestMessage
    # envelope does the unknown-key rejection outside this gate's scope.
    assert_strict_request_models(app, expect_request_bodies=False)
