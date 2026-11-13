"""A key's declared grants are the Registry's to read, never a service's.

Provenance: CASE-1220. Since every permission became the Registry's answer,
grants declared on a config key are inert outside the Registry: a key the
Registry never imported has none there, whatever the config says. Loading
such a key now says so, and the provider's own namespace check, which still
widened a key's reach by its grants, is gone.
"""

import json
import logging

from wip_auth.config import AuthConfig
from wip_auth.providers.api_key import APIKeyProvider


def _load(caplog, keys):
    cfg = AuthConfig(_env_file=None, api_keys_json=json.dumps(keys))
    with caplog.at_level(logging.WARNING, logger="wip_auth"):
        return cfg.load_api_keys()


def test_a_key_declaring_grants_is_warned_about(caplog):
    keys = _load(caplog, [
        {"name": "agent", "key": "kR7mX2pQ9vL4nB8wZ3cF6a", "owner": "o", "groups": ["g"],
         "namespaces": ["kb", "library"], "grants": {"kb": "write", "library": "read"}},
    ])
    assert "agent" in [k.name for k in keys]  # warned, still loaded
    assert "API key 'agent' declares grants (kb=write, library=read)" in caplog.text
    assert "which does not apply them" in caplog.text
    assert "does not read them" not in caplog.text


def test_a_key_without_grants_is_not(caplog):
    _load(caplog, [
        {"name": "plain", "key": "kR7mX2pQ9vL4nB8wZ3cF6a", "owner": "o", "groups": ["g"],
         "namespaces": ["kb"]},
    ])
    assert "declares grants" not in caplog.text


def test_the_provider_has_no_namespace_check_of_its_own():
    assert not hasattr(APIKeyProvider, "check_namespace_access")
