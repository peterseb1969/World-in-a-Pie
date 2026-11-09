"""reporting-sync names the platform groups only through wip_auth.

A source walk fails here when code spells "wip-admins" or "wip-services"
inline: every such copy can drift from the set the Registry and the other
services check. Use wip_auth.ADMIN_GROUP, SERVICES_GROUP or
PRIVILEGED_GROUPS.
"""

from pathlib import Path

from wip_auth.testing import assert_no_privileged_group_literals

SOURCE = Path(__file__).resolve().parents[1] / "src" / "reporting_sync"


def test_no_inline_group_names():
    assert_no_privileged_group_literals(SOURCE)
