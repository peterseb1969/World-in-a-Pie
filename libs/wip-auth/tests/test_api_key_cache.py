"""API-key verification caches.

bcrypt costs about 170 ms per stored hash at its default cost, and a key is
matched by checking it against the stored hashes in turn. These tests pin
that each key is checked against the hashes once per process, that cache
maintenance (key added, removed, runtime-key sync) keeps what is still
valid, that a repeated wrong key is not re-checked, and that the trusted-header
provider shares the API-key provider's cache.
"""

import bcrypt
import pytest
from starlette.requests import Request

from wip_auth import APIKeyRecord, AuthConfig, TrustedHeaderProvider, create_providers_from_config
from wip_auth.providers import api_key as api_key_module
from wip_auth.providers.api_key import APIKeyProvider, hash_api_key


@pytest.fixture(autouse=True)
def cheap_bcrypt(monkeypatch):
    original = bcrypt.gensalt
    monkeypatch.setattr(bcrypt, "gensalt", lambda rounds=4, prefix=b"2b": original(4, prefix))


@pytest.fixture
def checks(monkeypatch):
    """Counts bcrypt checks made by the API-key provider."""
    calls: list[str] = []
    original = api_key_module.verify_api_key

    def counting(key, key_hash, salt="wip_auth_salt"):
        calls.append(key_hash)
        return original(key, key_hash, salt)

    monkeypatch.setattr(api_key_module, "verify_api_key", counting)
    return calls


def key(name: str, secret: str, groups: list[str] | None = None) -> APIKeyRecord:
    return APIKeyRecord(name=name, key_hash=hash_api_key(secret), owner=f"system:{name}", groups=groups or [])


def request(headers: dict) -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    })


def test_a_known_key_is_checked_once(checks):
    provider = APIKeyProvider([key("a", "sa"), key("b", "sb")])
    assert provider.lookup("sb").name == "b"
    checks.clear()
    assert provider.lookup("sb").name == "b"
    assert checks == []


def test_adding_a_key_keeps_verified_entries(checks):
    provider = APIKeyProvider([key("a", "sa")])
    provider.lookup("sa")
    provider.add_key(key("b", "sb"))
    checks.clear()
    assert provider.lookup("sa").name == "a"
    assert checks == []


def test_removing_a_key_evicts_only_that_key(checks):
    a, b = key("a", "sa"), key("b", "sb")
    provider = APIKeyProvider([a, b])
    provider.lookup("sa")
    provider.lookup("sb")
    provider.remove_key(a.key_hash)

    assert provider.lookup("sa") is None
    checks.clear()
    assert provider.lookup("sb").name == "b"
    assert checks == []


def test_sync_keeps_entries_and_picks_up_changed_groups(checks):
    config_key = key("config", "sc", ["wip-admins"])
    runtime = key("runtime", "sr", ["wip-viewers"])
    gone = key("gone", "sg")
    provider = APIKeyProvider([config_key, runtime, gone])
    for secret in ("sc", "sr", "sg"):
        provider.lookup(secret)

    updated = runtime.model_copy(update={"groups": ["wip-editors"]})
    provider.replace_runtime_keys([updated], config_key_names={"config"})

    checks.clear()
    assert provider.lookup("sr").groups == ["wip-editors"]
    assert provider.lookup("sc").name == "config"
    assert checks == []
    assert provider.lookup("sg") is None


def test_a_repeated_wrong_key_is_not_rechecked(checks):
    provider = APIKeyProvider([key("a", "sa"), key("b", "sb")])
    assert provider.lookup("wrong") is None
    assert len(checks) == 2
    checks.clear()
    assert provider.lookup("wrong") is None
    assert checks == []


def test_a_rejected_key_that_is_then_added_is_accepted():
    provider = APIKeyProvider([key("a", "sa")])
    assert provider.lookup("snew") is None
    provider.add_key(key("new", "snew"))
    assert provider.lookup("snew").name == "new"


def test_a_rejection_expires(monkeypatch, checks):
    clock = [1000.0]
    monkeypatch.setattr(api_key_module.time, "monotonic", lambda: clock[0])
    provider = APIKeyProvider([key("a", "sa")])
    provider.lookup("wrong")
    clock[0] += api_key_module._REJECTED_TTL_SECONDS + 1
    checks.clear()
    provider.lookup("wrong")
    assert len(checks) == 1


@pytest.mark.asyncio
async def test_trusted_headers_use_the_shared_cache(checks):
    api_provider = APIKeyProvider([key("proxy", "sp", ["wip-admins"])])
    trusted = TrustedHeaderProvider(keys=[], key_provider=api_provider)
    headers = {"X-WIP-User": "u@wip.local", "X-WIP-Groups": "wip-viewers", "X-API-Key": "sp"}

    assert await trusted.authenticate(request(headers)) is not None
    checks.clear()
    assert await trusted.authenticate(request(headers)) is not None
    assert checks == []


def test_config_wires_one_shared_key_provider(monkeypatch):
    monkeypatch.setenv("WIP_AUTH_MODE", "dual")
    monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "legacy-secret")
    monkeypatch.setenv("WIP_AUTH_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("WIP_AUTH_JWT_ISSUER_URL", "")
    providers = create_providers_from_config(AuthConfig())

    trusted = next(p for p in providers if isinstance(p, TrustedHeaderProvider))
    api_provider = next(p for p in providers if isinstance(p, APIKeyProvider))
    assert trusted._key_provider is api_provider
    assert providers.index(trusted) < providers.index(api_provider)
