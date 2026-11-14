"""API key loading, pinned before load_api_keys' refactor.

Provenance: CASE-1161. The suite never reached these branches: a JSON key
replacing an earlier key of the same name (later sources override earlier);
unparseable WIP_AUTH_API_KEYS_JSON (warned, the other sources still load); a
keys file that does not exist (skipped); and the warning for a key with no
namespace scope outside the privileged groups.
"""

import json
import logging

from wip_auth.config import AuthConfig


def _cfg(**kwargs) -> AuthConfig:
    return AuthConfig(_env_file=None, **kwargs)


def test_a_later_source_replaces_a_key_of_the_same_name(tmp_path, caplog):
    json_keys = [{"name": "legacy", "key": "json-legacy", "owner": "o", "groups": ["wip-services"]},
                 {"name": "app", "key": "json-app", "owner": "o", "groups": ["g"], "namespaces": ["ns1"]}]
    file_keys = {"keys": [{"name": "app", "key": "file-app", "owner": "f", "groups": ["g"], "namespaces": ["ns2"]},
                          {"name": "loose", "key": "file-loose", "owner": "f", "groups": ["g"]}]}
    path = tmp_path / "keys.json"
    path.write_text(json.dumps(file_keys))
    with caplog.at_level(logging.WARNING, logger="wip_auth"):
        keys = _cfg(legacy_api_key="env-legacy", api_keys_json=json.dumps(json_keys),
                    api_keys_file=str(path)).load_api_keys()
    # The JSON "legacy" does not replace the master key (CASE-1266): the
    # name is the master key's while WIP_AUTH_LEGACY_API_KEY is set.
    assert [(k.name, k.owner, k.namespaces) for k in keys] == [
        ("legacy", "system:legacy", None), ("app", "f", ["ns2"]), ("loose", "f", None),
    ]
    assert "API key 'legacy' in WIP_AUTH_API_KEYS_JSON or the key file not loaded" in caplog.text
    assert "API key 'loose' has no namespace scope" in caplog.text
    assert "'app' has no namespace scope" not in caplog.text


def test_without_a_master_key_a_configured_legacy_key_loads():
    json_keys = [{"name": "legacy", "key": "json-legacy", "owner": "o", "groups": ["wip-services"]}]
    keys = _cfg(legacy_api_key=None, api_keys_json=json.dumps(json_keys)).load_api_keys()
    assert [(k.name, k.owner) for k in keys] == [("legacy", "o")]


def test_bad_json_is_warned_and_a_missing_file_skipped(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="wip_auth"):
        keys = _cfg(legacy_api_key="env-legacy", api_keys_json="[not json",
                    api_keys_file=str(tmp_path / "absent.json")).load_api_keys()
    assert [k.name for k in keys] == ["legacy"]
    assert "Failed to parse WIP_AUTH_API_KEYS_JSON" in caplog.text
