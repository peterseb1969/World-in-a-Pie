"""Config keys that share a plaintext are not loaded.

A presented key authenticates as the first record it matches, and the match
is cached. Two config keys with one plaintext therefore authenticate as
whichever a process matched first. Every service reads the same key file, so
the loader refuses the value: none of the keys sharing it load. The master
key (WIP_AUTH_LEGACY_API_KEY) is the exception, since services authenticate to
each other with it; it stays, and the keys sharing its value are dropped.
"""

import json
import logging
from pathlib import Path

import pytest

from wip_auth import AuthConfig, reset_auth_config
from wip_auth.providers.api_key import APIKeyProvider, hash_api_key


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    # AuthConfig reads WIP_AUTH_* from the environment; these tests define
    # every key themselves.
    for var in ("WIP_AUTH_LEGACY_API_KEY", "WIP_AUTH_API_KEYS_JSON", "WIP_AUTH_API_KEYS_FILE"):
        monkeypatch.delenv(var, raising=False)
    reset_auth_config()
    yield
    reset_auth_config()


def _key(name: str, key: str, **extra) -> dict:
    return {"name": name, "key": key, "owner": "system", "namespaces": ["ns"], **extra}


def _config(tmp_path: Path, file_keys: list[dict], **kw) -> AuthConfig:
    path = tmp_path / "api-keys.json"
    path.write_text(json.dumps({"keys": file_keys}))
    return AuthConfig(api_keys_file=str(path), **kw)


def _names(config: AuthConfig) -> list[str]:
    return sorted(k.name for k in config.load_api_keys())


def test_distinct_plaintexts_all_load(tmp_path: Path):
    config = _config(tmp_path, [_key("a", "one"), _key("b", "two")], legacy_api_key="master")
    assert _names(config) == ["a", "b", "legacy"]


def test_keys_sharing_a_plaintext_do_not_load(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    config = _config(tmp_path, [_key("a", "same"), _key("b", "same"), _key("c", "other")])
    with caplog.at_level(logging.ERROR, logger="wip_auth"):
        assert _names(config) == ["c"]
    assert "API keys a, b share one plaintext; a, b not loaded" in caplog.text


def test_a_key_sharing_the_master_keys_plaintext_is_dropped_the_master_stays(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
):
    config = _config(tmp_path, [_key("app-x", "master")], legacy_api_key="master")
    with caplog.at_level(logging.ERROR, logger="wip_auth"):
        assert _names(config) == ["legacy"]
    assert "app-x, legacy share one plaintext; app-x not loaded" in caplog.text


def test_a_source_replacing_a_key_by_name_is_not_a_shared_value(tmp_path: Path):
    """The env JSON and the file both define `a` with one value: one key."""
    config = _config(tmp_path, [_key("a", "same")], api_keys_json=json.dumps([_key("a", "same")]))
    assert _names(config) == ["a"]


def test_values_are_compared_across_sources(tmp_path: Path):
    """`b` from the env JSON shares a value with `a` from the key file."""
    config = _config(
        tmp_path, [_key("a", "same")],
        api_keys_json=json.dumps([_key("b", "same"), _key("c", "other")]),
    )
    assert _names(config) == ["c"]


def test_a_pre_hashed_key_is_not_compared(tmp_path: Path):
    hashed = {"name": "h", "key_hash": hash_api_key("same"), "owner": "system", "namespaces": ["ns"]}
    config = _config(tmp_path, [hashed, _key("a", "same")])
    assert _names(config) == ["a", "h"]


def test_every_provider_answers_the_same_for_a_shared_value(tmp_path: Path):
    """What the services see: the shared value authenticates as nobody (or,
    shared with the master key, always as the master key) — never as
    whichever key one process happened to match first."""
    config = _config(
        tmp_path,
        [_key("a", "same"), _key("b", "same"), _key("app-x", "master")],
        legacy_api_key="master",
    )
    for _ in range(2):  # two independently started services
        provider = APIKeyProvider(keys=config.load_api_keys(), hash_salt=config.api_key_hash_salt)
        assert provider.identify_key("same") is None
        identity = provider.identify_key("master")
        assert identity is not None and identity.username == "legacy"
