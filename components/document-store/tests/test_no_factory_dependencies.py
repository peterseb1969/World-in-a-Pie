"""Every route dependency on this service is a real dependency, never an
uncalled factory.

An uncalled dependency factory — Depends(require_admin) instead of
Depends(require_admin()) — is accepted by FastAPI, which hands the
returned closure to the endpoint as the dependency's VALUE: the check
never runs and every authenticated caller passes. The Registry's entire
admin surface shipped that way for eight months, privilege escalation
included; happy-path tests cannot see it because an authorized caller
passes either way. This gate fails the suite the moment any route here
is wired that way.
"""

from document_store.main import app
from wip_auth.testing import assert_no_factory_dependencies


def test_no_factory_dependencies():
    assert_no_factory_dependencies(app)
