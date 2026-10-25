"""Every bulk error item template-store builds names an error_code.

Callers branch on the code, never on the English message; a source walk
fails here when a new error item is born without one, whichever file it
is in. Build items with services.errors.error_fields(exc), or name the code.
"""

from pathlib import Path

import template_store
from wip_auth.testing import assert_error_items_carry_codes


def test_error_items_carry_codes():
    assert_error_items_carry_codes(Path(template_store.__file__).parent)
