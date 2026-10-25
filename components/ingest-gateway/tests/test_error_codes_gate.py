"""Every bulk error item ingest-gateway builds names an error_code.

Callers branch on the code, never on the English message; a source walk
fails here when a new error item is born without one, whichever file it
is in. The service builds no error items today; the gate catches the
first one born without a code.
"""

from pathlib import Path

import ingest_gateway
from wip_auth.testing import assert_error_items_carry_codes


def test_error_items_carry_codes():
    assert_error_items_carry_codes(Path(ingest_gateway.__file__).parent, expect_error_items=False)
