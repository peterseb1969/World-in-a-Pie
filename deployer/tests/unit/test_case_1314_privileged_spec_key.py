"""A spec key never carries wip-admins or wip-services.

A key in either group acts on every namespace and is never scoped, while a
spec key always carries `namespaces`; the Registry refuses a key file that
holds such a key and imports none of it. So the spec refuses the group first.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from wip_deploy.spec.deployment import ADMIN_GROUP, SERVICES_GROUP, SpecAPIKey


@pytest.mark.parametrize("group", [ADMIN_GROUP, SERVICES_GROUP])
def test_a_spec_key_refuses_a_privileged_group(group):
    with pytest.raises(ValidationError, match="never scoped"):
        SpecAPIKey(name="agent", namespaces=["kb"], groups=["wip-users", group])


def test_a_spec_key_takes_other_groups():
    key = SpecAPIKey(name="agent", namespaces=["kb"], groups=["wip-users", "wip-app-edge"])
    assert key.groups == ["wip-users", "wip-app-edge"]
