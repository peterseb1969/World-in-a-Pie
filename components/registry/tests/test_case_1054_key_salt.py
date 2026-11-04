"""The runtime-key salt is the Registry's, per stack, and travels with the
system backup.

A new stack generates a random salt; a stack that already holds keys keeps
the configured one. Key creation, the key import and the shadow check hash
under it, key sync hands it to the services, and a system restore onto a
fresh stack adopts the archive's salt while a stack with keys of its own
refuses another one.
"""

import asyncio
import json
import uuid

import pytest

from registry.main import providers as _app_providers
from registry.models.api_key import StoredAPIKey
from registry.models.grant import NamespaceGrant
from registry.models.reservation import RuntimeReservation
from registry.services import key_import, key_salt, key_shadowing
from registry.services.key_import import CANARY_KEY_NAME, KEY_FILE_ENV
from registry.services.system_backup import salt_fingerprint
from wip_auth import APIKeyProvider, get_auth_config
from wip_auth.key_sync import RUNTIME_SALT_HEADER
from wip_auth.providers.api_key import hash_api_key, verify_api_key

KEYS = "/api/registry/api-keys"
BACKUP = "/api/registry/system-backup"
RESTORE = "/api/registry/system-restore"
CONFIGURED = get_auth_config().api_key_hash_salt


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def provider():
    provider = next(p for p in _app_providers if isinstance(p, APIKeyProvider))
    key_shadowing.reset_for_tests()
    yield provider
    provider.set_runtime_salt(provider.hash_salt)
    key_shadowing.reset_for_tests()


async def _drop_salt() -> None:
    key_salt.reset_for_tests()
    await key_salt._collection().delete_many({})


async def _new_key(client, headers, name: str | None = None) -> tuple[str, str]:
    name = name or _p("console")
    resp = await client.post(KEYS, headers=headers, json={"name": name, "namespaces": ["default"]})
    assert resp.status_code == 201, resp.text
    return name, resp.json()["plaintext_key"]


async def _authenticates(client, plaintext: str) -> bool:
    resp = await client.get("/api/registry/my/namespaces", headers={"X-API-Key": plaintext})
    return resp.status_code == 200


def _key_file(monkeypatch, tmp_path, *entries: dict) -> None:
    path = tmp_path / "api-keys.json"
    canary = {"name": CANARY_KEY_NAME, "key": "canary-plaintext-0001", "namespaces": [], "grants": {}}
    path.write_text(json.dumps({"keys": [canary, *entries]}))
    monkeypatch.setenv(KEY_FILE_ENV, str(path))


# --- the stored salt -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_stack_generates_a_salt_once(client):
    await StoredAPIKey.delete_all()
    salt = await key_salt.ensure()
    assert salt != CONFIGURED and len(salt) >= 40
    key_salt.reset_for_tests()
    assert await key_salt.ensure() == salt


@pytest.mark.asyncio
async def test_concurrent_first_starts_agree_on_one_salt(client):
    await StoredAPIKey.delete_all()
    results = await asyncio.gather(*(key_salt.ensure() for _ in range(5)))
    assert len(set(results)) == 1
    assert await key_salt._collection().count_documents({}) == 1


@pytest.mark.asyncio
async def test_a_stack_with_keys_keeps_the_configured_salt(client, auth_headers):
    _, plaintext = await _new_key(client, auth_headers)
    assert await key_salt.ensure() == CONFIGURED
    assert await _authenticates(client, plaintext)


# --- hashing and handing it out -------------------------------------------


@pytest.mark.asyncio
async def test_keys_are_hashed_under_the_stored_salt_and_sync_hands_it_out(
    client, auth_headers, provider,
):
    await StoredAPIKey.delete_all()
    salt = await key_salt.ensure()
    provider.set_runtime_salt(salt)

    name, plaintext = await _new_key(client, auth_headers)
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    assert verify_api_key(plaintext, doc.key_hash, salt)
    assert not verify_api_key(plaintext, doc.key_hash, CONFIGURED)
    assert await _authenticates(client, plaintext)

    resp = await client.get(f"{KEYS}/sync", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.headers[RUNTIME_SALT_HEADER] == salt
    assert name in {k["name"] for k in resp.json()}


@pytest.mark.asyncio
async def test_the_import_hashes_file_keys_under_the_stored_salt(
    client, provider, monkeypatch, tmp_path,
):
    await StoredAPIKey.delete_all()
    salt = await key_salt.ensure()
    name = _p("app")
    _key_file(monkeypatch, tmp_path, {"name": name, "key": "pt-" + name, "namespaces": []})
    await key_import.run_pass(provider)
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    assert verify_api_key("pt-" + name, doc.key_hash, salt)


@pytest.mark.asyncio
async def test_shadowing_still_fires_when_the_salts_differ(client, provider):
    """A runtime key with the master key's plaintext, hashed under a runtime
    salt the process does not hash its config keys with, is still shadowed."""
    await StoredAPIKey.delete_all()
    salt = await key_salt.ensure()
    assert salt != provider.hash_salt
    provider.set_runtime_salt(salt)
    name = _p("pasted")
    master = next(iter(provider.config_key_names()))
    plaintext = next(
        k for k in provider.iter_keys() if k.name == master
    )._plaintext
    await StoredAPIKey(name=name, key_hash=hash_api_key(plaintext, salt)).insert()

    shadows = await key_shadowing.reconcile(provider)
    assert shadows.get(name) == master


# --- the system backup -----------------------------------------------------


async def _wipe(provider: APIKeyProvider) -> None:
    for doc in await StoredAPIKey.find_all().to_list():
        provider.remove_key(doc.key_hash)
    await StoredAPIKey.delete_all()
    await NamespaceGrant.delete_all()
    await RuntimeReservation.delete_all()


@pytest.mark.asyncio
async def test_the_archive_carries_the_salt(client, auth_headers):
    await StoredAPIKey.delete_all()
    salt = await key_salt.ensure()
    archive = (await client.post(BACKUP, headers=auth_headers)).json()
    assert archive["source"]["salt"] == salt
    assert archive["source"]["salt_fingerprint"] == salt_fingerprint(salt)


@pytest.mark.asyncio
async def test_a_fresh_stack_adopts_the_archive_salt(
    client, auth_headers, provider, monkeypatch, tmp_path,
):
    # The old stack: its own salt, a Console key.
    await StoredAPIKey.delete_all()
    old_salt = await key_salt.ensure()
    provider.set_runtime_salt(old_salt)
    name, plaintext = await _new_key(client, auth_headers)
    archive = (await client.post(BACKUP, headers=auth_headers)).json()

    # Disaster: the Registry's database is gone. The new stack starts empty,
    # generates a salt of its own, and imports its key file.
    await _wipe(provider)
    await _drop_salt()
    new_salt = await key_salt.ensure()
    assert new_salt != old_salt
    provider.set_runtime_salt(new_salt)
    managed = _p("app")
    _key_file(monkeypatch, tmp_path, {"name": managed, "key": "pt-" + managed, "namespaces": []})
    await key_import.run_pass(provider)
    assert not await _authenticates(client, plaintext)

    # A dry run reports the adoption and changes nothing.
    resp = await client.post(RESTORE, json=archive, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["salt_adopted"] is True
    assert key_salt.current_salt() == new_salt

    resp = await client.post(RESTORE, json=archive, params={"dry_run": "false"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["salt_adopted"] is True
    assert name in report["api_keys"]["created"]
    assert any("adopted" in w for w in report["warnings"])

    assert key_salt.current_salt() == old_salt
    key_salt.reset_for_tests()
    assert await key_salt.ensure() == old_salt  # persisted, not only in memory
    assert provider.runtime_salt == old_salt
    assert await _authenticates(client, plaintext)
    # The managed key was re-hashed under the adopted salt.
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == managed)
    assert verify_api_key("pt-" + managed, doc.key_hash, old_salt)
    assert await _authenticates(client, "pt-" + managed)

    resp = await client.get(f"{KEYS}/sync", headers=auth_headers)
    assert resp.headers[RUNTIME_SALT_HEADER] == old_salt


@pytest.mark.asyncio
async def test_a_stack_with_keys_of_its_own_refuses_another_salt(client, auth_headers, provider):
    await StoredAPIKey.delete_all()
    salt = await key_salt.ensure()
    provider.set_runtime_salt(salt)
    _, own = await _new_key(client, auth_headers)
    archive = (await client.post(BACKUP, headers=auth_headers)).json()
    archive["source"]["salt"] = "other-stack"
    archive["source"]["salt_fingerprint"] = salt_fingerprint("other-stack")

    resp = await client.post(RESTORE, json=archive, params={"dry_run": "false"}, headers=auth_headers)
    assert resp.status_code == 409
    assert key_salt.current_salt() == salt
    assert await _authenticates(client, own)


@pytest.mark.asyncio
async def test_an_archive_without_the_salt_cannot_be_adopted(client, auth_headers, provider):
    await StoredAPIKey.delete_all()
    archive = (await client.post(BACKUP, headers=auth_headers)).json()
    archive["source"].pop("salt")
    archive["source"]["salt_fingerprint"] = salt_fingerprint("other-stack")
    resp = await client.post(RESTORE, json=archive, headers=auth_headers)
    assert resp.status_code == 409
    assert "does not carry the salt" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_salt_that_does_not_match_its_fingerprint_is_invalid(client, auth_headers):
    archive = (await client.post(BACKUP, headers=auth_headers)).json()
    archive["source"]["salt"] = "tampered"
    resp = await client.post(RESTORE, json=archive, headers=auth_headers)
    assert resp.status_code == 422
