"""A runtime key with the same plaintext as a config key is shadowed.

A presented key authenticates as the first record it matches, config keys
first, and each process caches the match. A runtime key sharing a config
key's plaintext therefore authenticates as whichever key a process matched
first, so services started at different times disagree about the caller.
The Registry, which sees both the config keys' plaintext prehashes and the
runtime hashes, withholds such a runtime key from /api-keys/sync and from its
own provider, and lists it with the config key that shadows it. The config
key wins.
"""

import pytest
from httpx import AsyncClient

from registry.main import providers as _app_providers
from registry.services import key_shadowing
from wip_auth import APIKeyProvider, APIKeyRecord
from wip_auth.providers.api_key import api_key_prehash, hash_api_key

BASE = "/api/registry/api-keys"


def _provider() -> APIKeyProvider:
    return next(p for p in _app_providers if isinstance(p, APIKeyProvider))


def _config_record(provider: APIKeyProvider, name: str, plaintext: str) -> APIKeyRecord:
    record = APIKeyRecord(
        name=name,
        key_hash=hash_api_key(plaintext, provider.hash_salt),
        namespaces=["default"],
        grants={"default": "read"},
    )
    # What wip-auth's config loading keeps for a plaintext config key.
    record._plaintext_prehash = api_key_prehash(plaintext, provider.hash_salt)
    record._plaintext = plaintext
    return record


@pytest.fixture
def provider():
    provider = _provider()
    names = provider.config_key_names()
    original = [k for k in provider.iter_keys() if k.name in names]
    key_shadowing.reset_for_tests()
    yield provider
    provider.reload_config_keys(original)
    key_shadowing.reset_for_tests()


async def _create(client: AsyncClient, headers: dict, name: str) -> str:
    response = await client.post(
        BASE, json={"name": name, "namespaces": ["default"]}, headers=headers,
    )
    assert response.status_code == 201
    return response.json()["plaintext_key"]


async def _synced(client: AsyncClient, headers: dict) -> set[str]:
    response = await client.get(f"{BASE}/sync", headers=headers)
    assert response.status_code == 200
    return {r["name"] for r in response.json()}


async def _listed(client: AsyncClient, headers: dict, name: str) -> dict:
    response = await client.get(f"{BASE}/{name}", headers=headers)
    assert response.status_code == 200
    return response.json()


def _add_config_key(provider: APIKeyProvider, record: APIKeyRecord) -> None:
    names = provider.config_key_names()
    current = [k for k in provider.iter_keys() if k.name in names]
    provider.reload_config_keys([*current, record])


@pytest.mark.asyncio
async def test_a_shadowed_runtime_key_leaves_sync_and_the_provider_resolves_the_config_key(
    client: AsyncClient, auth_headers: dict, provider: APIKeyProvider,
):
    plaintext = await _create(client, auth_headers, "pasted-runtime")
    # The Registry's own process matches the plaintext as the runtime key and
    # caches that — the history that made services disagree.
    assert provider.lookup(plaintext).name == "pasted-runtime"

    _add_config_key(provider, _config_record(provider, "app-x", plaintext))
    # The config reload alone keeps the cached runtime match.
    assert provider.lookup(plaintext).name == "pasted-runtime"

    assert await key_shadowing.reconcile(provider) == {"pasted-runtime": "app-x"}

    assert provider.lookup(plaintext).name == "app-x"
    assert "pasted-runtime" not in await _synced(client, auth_headers)
    listed = await _listed(client, auth_headers, "pasted-runtime")
    assert listed["shadowed_by"] == "app-x"
    # The runtime key is kept, so an operator can see and revoke it.
    assert listed["source"] == "runtime"


@pytest.mark.asyncio
async def test_an_unrelated_runtime_key_is_untouched(
    client: AsyncClient, auth_headers: dict, provider: APIKeyProvider,
):
    plaintext = await _create(client, auth_headers, "own-value")
    _add_config_key(provider, _config_record(provider, "app-y", "a-different-value"))

    assert await key_shadowing.reconcile(provider) == {}

    assert provider.lookup(plaintext).name == "own-value"
    assert "own-value" in await _synced(client, auth_headers)
    assert (await _listed(client, auth_headers, "own-value"))["shadowed_by"] is None


@pytest.mark.asyncio
async def test_a_key_no_longer_shadowed_returns(
    client: AsyncClient, auth_headers: dict, provider: APIKeyProvider,
):
    plaintext = await _create(client, auth_headers, "comes-back")
    names = provider.config_key_names()
    before = [k for k in provider.iter_keys() if k.name in names]
    _add_config_key(provider, _config_record(provider, "app-z", plaintext))
    await key_shadowing.reconcile(provider)
    assert provider.lookup(plaintext).name == "app-z"

    # The config key gets a new value (here: leaves the key file).
    provider.reload_config_keys(before)
    assert await key_shadowing.reconcile(provider) == {}

    assert provider.lookup(plaintext).name == "comes-back"
    assert "comes-back" in await _synced(client, auth_headers)
    assert (await _listed(client, auth_headers, "comes-back"))["shadowed_by"] is None


@pytest.mark.asyncio
async def test_updating_a_shadowed_key_does_not_put_it_back(
    client: AsyncClient, auth_headers: dict, provider: APIKeyProvider,
):
    plaintext = await _create(client, auth_headers, "edited-while-shadowed")
    _add_config_key(provider, _config_record(provider, "app-w", plaintext))
    await key_shadowing.reconcile(provider)

    response = await client.patch(
        f"{BASE}/edited-while-shadowed",
        json={"description": "still shadowed"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["shadowed_by"] == "app-w"
    assert not any(k.name == "edited-while-shadowed" for k in provider.iter_keys())
    assert provider.lookup(plaintext).name == "app-w"


def test_compute_shadows_checks_each_pair_once_and_prunes_the_cache():
    salt = "s"
    runtime = [("r1", hash_api_key("one", salt)), ("r2", hash_api_key("two", salt))]
    config = {"c-two": api_key_prehash("two", salt), "c-three": api_key_prehash("three", salt)}
    cache: dict = {}

    assert key_shadowing.compute_shadows(config, runtime, cache) == {"r2": "c-two"}
    # r1 against both config keys, r2 against c-three then c-two (name order).
    assert len(cache) == 4

    # A config key that leaves takes its pairs out of the cache.
    del config["c-three"]
    assert key_shadowing.compute_shadows(config, runtime, cache) == {"r2": "c-two"}
    assert len(cache) == 2
