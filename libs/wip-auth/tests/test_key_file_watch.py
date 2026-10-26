"""Config-file API keys apply without a restart (CASE-884).

The deployer renders every declared key into WIP_AUTH_API_KEYS_FILE. Services
read it once at start, so adding, removing or rotating any declared key
restarted every service that reads the file. The watcher re-reads the file and
swaps the config keys into the running provider.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from wip_auth import APIKeyProvider, AuthConfig, create_providers_from_config
from wip_auth.key_file_watch import APIKeysFileWatcher
from wip_auth.models import APIKeyRecord
from wip_auth.providers.api_key import hash_api_key


def _write(path: Path, keys: dict[str, str]) -> None:
    path.write_text(json.dumps({"keys": [
        {"name": name, "key": secret, "owner": "system", "groups": ["wip-services"]}
        for name, secret in keys.items()
    ]}))


def _setup(tmp_path: Path, keys: dict[str, str]) -> tuple[AuthConfig, APIKeyProvider, APIKeysFileWatcher]:
    path = tmp_path / "api-keys.json"
    _write(path, keys)
    config = AuthConfig(mode="api_key_only", api_keys_file=str(path), api_keys_file_reload_seconds=0)
    provider = next(p for p in create_providers_from_config(config) if isinstance(p, APIKeyProvider))
    return config, provider, APIKeysFileWatcher(path, config.load_api_keys, provider)


def test_rotation_and_addition_apply_without_rebuilding(tmp_path: Path) -> None:
    config, provider, watcher = _setup(tmp_path, {"agent": "old-secret"})
    assert provider.lookup("old-secret") is not None

    _write(Path(config.api_keys_file), {"agent": "new-secret", "app-kb": "kb-secret"})
    assert watcher.check_once() is True

    assert provider.lookup("old-secret") is None
    assert provider.lookup("new-secret").name == "agent"
    assert provider.lookup("kb-secret").name == "app-kb"
    # Env-defined keys (here the test env's legacy key) stay config keys too.
    assert {"agent", "app-kb"} <= provider.config_key_names()


def test_unchanged_file_is_not_reapplied(tmp_path: Path) -> None:
    _, _, watcher = _setup(tmp_path, {"agent": "s"})
    assert watcher.check_once() is False


def test_malformed_file_keeps_the_previous_keys(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    config, provider, watcher = _setup(tmp_path, {"agent": "secret"})
    Path(config.api_keys_file).write_text('{"keys": [{"name": "agent", "ke')

    with caplog.at_level(logging.CRITICAL, logger="wip_auth.key_file_watch"):
        assert watcher.check_once() is False
        # A half-written file may be caught mid-write: no alarm yet.
        assert not caplog.records
        assert watcher.check_once() is False
        assert "does not parse" in caplog.text
    assert provider.lookup("secret").name == "agent"


def test_missing_file_keeps_the_previous_keys(tmp_path: Path) -> None:
    config, provider, watcher = _setup(tmp_path, {"agent": "secret"})
    Path(config.api_keys_file).unlink()
    assert watcher.check_once() is False
    assert provider.lookup("secret").name == "agent"


def test_key_sync_keeps_a_reloaded_config_key(tmp_path: Path) -> None:
    """The sync preserves config keys by name: a key the reload added must
    count as a config key, or the next 30 s poll drops it."""
    config, provider, watcher = _setup(tmp_path, {"agent": "a"})
    runtime = APIKeyRecord(name="console-key", key_hash=hash_api_key("r"), groups=[])
    provider.add_key(runtime)

    _write(Path(config.api_keys_file), {"agent": "a", "app-kb": "kb"})
    watcher.check_once()
    provider.replace_runtime_keys([runtime])

    assert provider.lookup("kb").name == "app-kb"
    assert provider.lookup("r").name == "console-key"


def test_reload_keeps_runtime_keys(tmp_path: Path) -> None:
    config, provider, watcher = _setup(tmp_path, {"agent": "a"})
    provider.add_key(APIKeyRecord(name="console-key", key_hash=hash_api_key("r"), groups=[]))
    _write(Path(config.api_keys_file), {"agent": "b"})
    watcher.check_once()
    assert provider.lookup("r").name == "console-key"
    assert "console-key" not in provider.config_key_names()


def test_factory_starts_a_watcher_only_when_asked(tmp_path: Path) -> None:
    path = tmp_path / "api-keys.json"
    _write(path, {"agent": "s"})
    on = AuthConfig(mode="api_key_only", api_keys_file=str(path), api_keys_file_reload_seconds=30)
    off = AuthConfig(mode="api_key_only", api_keys_file=str(path), api_keys_file_reload_seconds=0)
    watched = next(p for p in create_providers_from_config(on) if isinstance(p, APIKeyProvider))
    unwatched = next(p for p in create_providers_from_config(off) if isinstance(p, APIKeyProvider))
    assert isinstance(watched.file_watcher, APIKeysFileWatcher)
    watched.file_watcher.stop()
    assert unwatched.file_watcher is None
