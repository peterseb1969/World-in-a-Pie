"""Only the Registry reads the key file; it is the authority for every key.

The deployer renders the key file into a directory only the Registry's
container mounts, as WIP_REGISTRY_KEY_FILE. wip-auth does not load that
variable as config keys, so the Registry's imported records are the keys:
key sync serves them to every other service. An install rendered before the
move declares WIP_AUTH_API_KEYS_FILE instead; the import reads it too, and
the records stay dormant while wip-auth also loads it as config keys.
"""

import json
import uuid

import pytest

from registry.main import providers as _app_providers
from registry.services import key_import, key_shadowing
from registry.services.key_import import CANARY_KEY_NAME, KEY_FILE_ENV, LEGACY_KEY_FILE_ENV
from wip_auth import APIKeyProvider
from wip_auth.providers import api_key as api_key_module

KEYS = "/api/registry/api-keys"


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def provider():
    provider = next(p for p in _app_providers if isinstance(p, APIKeyProvider))
    names = provider.config_key_names()
    original = [k for k in provider.iter_keys() if k.name in names]
    key_shadowing.reset_for_tests()
    yield provider
    provider.reload_config_keys(original)
    key_shadowing.reset_for_tests()


def _write(tmp_path, *entries: dict) -> str:
    path = tmp_path / "api-keys.json"
    canary = {"name": CANARY_KEY_NAME, "key": "canary-plaintext-0001", "namespaces": [], "grants": {}}
    path.write_text(json.dumps({"keys": [canary, *entries]}))
    return str(path)


@pytest.mark.parametrize("env", [KEY_FILE_ENV, LEGACY_KEY_FILE_ENV])
@pytest.mark.asyncio
async def test_the_import_reads_the_key_file_under_either_name(client, monkeypatch, tmp_path, env):
    name = _p("app-k")
    monkeypatch.delenv(KEY_FILE_ENV, raising=False)
    monkeypatch.delenv(LEGACY_KEY_FILE_ENV, raising=False)
    monkeypatch.setenv(env, _write(tmp_path, {"name": name, "key": "pt-" + name, "namespaces": []}))
    report = await key_import.run_pass()
    assert name in report.keys_created


@pytest.mark.asyncio
async def test_after_the_switch_the_records_are_the_keys(client, auth_headers, monkeypatch, tmp_path, provider):
    # The Registry no longer loads the file as config keys: no config key
    # shares a name with the imported records, so none is dormant.
    app, spec = _p("app-k"), _p("spec-k")
    monkeypatch.setenv(KEY_FILE_ENV, _write(
        tmp_path,
        {"name": app, "key": "pt-" + app, "namespaces": ["default", "wip"],
         "grants": {"default": "write"}},
        {"name": spec, "key": "pt-" + spec, "namespaces": ["default"]},
    ))
    await key_import.run_pass(provider)

    synced = {r["name"] for r in (await client.get(f"{KEYS}/sync", headers=auth_headers)).json()}
    assert {app, spec, CANARY_KEY_NAME} <= synced
    # The Registry itself authenticates them from its records.
    assert provider.lookup("pt-" + app).name == app

    listed = {k["name"]: k for k in (await client.get(f"{KEYS}?page_size=1000", headers=auth_headers)).json()["items"]}
    assert (listed[app]["source"], listed[app]["managed_by"]) == ("runtime", "wip-deploy")
    # A key whose file entry declares grants shows them: they are the file's.
    assert listed[app]["grants"] == {"default": "write", "wip": "read"}
    # One that declares none keeps API grants, and shows none here.
    assert listed[spec]["grants"] is None
    got = (await client.get(f"{KEYS}/{app}", headers=auth_headers)).json()
    assert got["grants"] == {"default": "write", "wip": "read"}

    # The imported key authenticates against the Registry's API with its grants.
    resp = await client.get("/api/registry/namespaces", headers={"X-API-Key": "pt-" + app})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_a_changed_key_reaches_the_registrys_provider(client, monkeypatch, tmp_path, provider):
    name = _p("app-k")
    monkeypatch.setenv(KEY_FILE_ENV, _write(tmp_path, {"name": name, "key": "first-" + name, "namespaces": []}))
    await key_import.run_pass(provider)
    assert provider.lookup("first-" + name).name == name

    monkeypatch.setenv(KEY_FILE_ENV, _write(tmp_path, {"name": name, "key": "second-" + name, "namespaces": []}))
    await key_import.run_pass(provider)
    assert provider.lookup("second-" + name).name == name
    assert provider.lookup("first-" + name) is None
    # And a key the file drops leaves the provider.
    monkeypatch.setenv(KEY_FILE_ENV, _write(tmp_path))
    await key_import.run_pass(provider)
    assert provider.lookup("second-" + name) is None
    assert not any(api_key_module.verify_api_key("second-" + name, k.key_hash) for k in provider.iter_keys())
