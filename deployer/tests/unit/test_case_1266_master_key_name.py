"""No spec key may take the master key's name ("legacy"): every service
loads the master key under it, and a key-file key of that name would
replace the master key's record."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from wip_deploy.spec.deployment import MASTER_KEY_NAME, AuthSpec


def test_a_spec_key_named_like_the_master_key_is_refused():
    with pytest.raises(ValidationError, match="master key"):
        AuthSpec.model_validate({
            "mode": "api-key-only", "gateway": False, "users": [],
            "api_keys": [{"name": MASTER_KEY_NAME, "namespaces": ["kb"]}],
        })


def test_the_master_key_name_is_the_one_wip_auth_gives_it():
    # wip-deploy does not depend on wip-auth; the name is pinned here.
    assert MASTER_KEY_NAME == "legacy"
