"""Runtime API keys whose plaintext a config key, or the key file, already has.

A presented key authenticates as the first record it matches, config keys
first, and the match is cached per process. A runtime key that shares its
plaintext with a config key (an operator pasted a runtime key into a config
secret) therefore authenticates as whichever key a process matched first, so
services started at different times disagree about who the caller is.

The Registry is the one place that holds both sides: the config keys it
loads (with the SHA-256 prehash of each plaintext) and every runtime key's
hash. It checks each runtime hash against each config prehash, and a runtime
key that matches is *shadowed*: the config key wins (it is what a freshly
started process already resolves, and it is the key the deployer manages),
the runtime key is left out of /api-keys/sync and out of the Registry's own
provider, and the key listing names the config key that shadows it. Every
service's next key sync drops the runtime record and with it any cached
match, so all processes converge on the config key. The runtime key itself
is kept so an operator can see and revoke it.

Checking costs one bcrypt verification per (config key, runtime key) pair
that has not been checked before; results are cached by the pair's values,
so a steady-state pass is a database read and dictionary lookups. The check
runs at startup and then every RECONCILE_INTERVAL_SECONDS, which also picks
up config-key reloads (the key-file watcher swaps the provider's config keys
in place).

The key file's keys are also imported as Registry records (key_import),
marked managed by wip-deploy. Those are the file's own keys, so they are
never shadowed. The plaintexts compared are the loaded config keys' and the
last imported key file's: the two are the same set while the Registry loads
the file as config keys, and the file's alone once it no longer does.
"""

import asyncio
import logging

from wip_auth import APIKeyProvider, APIKeyRecord
from wip_auth.providers.api_key import prehash_matches

from ..models.api_key import StoredAPIKey

logger = logging.getLogger("registry.key_shadowing")

RECONCILE_INTERVAL_SECONDS = 10.0

# Serialises changes to the provider's runtime keys between the reconcile
# pass and the key endpoints, so a pass never re-adds a key an endpoint has
# just removed, or the reverse.
provider_lock = asyncio.Lock()

# runtime key name -> name of the config key that shadows it
_shadowed: dict[str, str] = {}
# (runtime key_hash, config prehash) -> whether they match
_match_cache: dict[tuple[str, bytes], bool] = {}


def shadowed_by(name: str) -> str | None:
    """The config key shadowing runtime key `name`, or None."""
    return _shadowed.get(name)


def is_shadowed(name: str) -> bool:
    return name in _shadowed


def runtime_record(doc: StoredAPIKey) -> APIKeyRecord:
    """The provider record for a stored runtime key."""
    return APIKeyRecord(
        name=doc.name,
        key_hash=doc.key_hash,
        owner=doc.owner,
        groups=doc.groups,
        description=doc.description,
        created_at=doc.created_at,
        expires_at=doc.expires_at,
        enabled=doc.enabled,
        namespaces=doc.namespaces,
    )


def compute_shadows(
    config_prehashes: dict[str, bytes],
    runtime: list[tuple[str, str]],
    cache: dict[tuple[str, bytes], bool],
) -> dict[str, str]:
    """Runtime key name -> the config key whose plaintext it matches.

    `runtime` is (name, key_hash) pairs. Config keys are tried in name order,
    so the answer is deterministic; config keys that share a plaintext are
    not loaded (wip-auth refuses them), so at most one can match. `cache` is
    read and filled, and pruned to the pairs that still exist.
    """
    shadows: dict[str, str] = {}
    live: set[tuple[str, bytes]] = set()
    configs = sorted(config_prehashes.items())
    for name, key_hash in runtime:
        for config_name, prehash in configs:
            pair = (key_hash, prehash)
            live.add(pair)
            match = cache.get(pair)
            if match is None:
                match = prehash_matches(prehash, key_hash)
                cache[pair] = match
            if match:
                shadows[name] = config_name
                break
    for pair in list(cache):
        if pair not in live:
            del cache[pair]
    return shadows


async def reconcile(provider: APIKeyProvider) -> dict[str, str]:
    """Recompute which runtime keys are shadowed and apply it to `provider`.

    Newly shadowed keys leave the provider (which evicts their cached
    matches); keys no longer shadowed return to it if they still exist and
    are enabled. Returns the current shadow map.
    """
    # Only API-made keys can be shadowed: a managed key is the key file's own
    # (key_import), so it cannot shadow itself. Its plaintext shadows an API
    # key of the same value as a loaded config key's does.
    from .key_import import file_prehashes

    docs = [
        d for d in await StoredAPIKey.find(StoredAPIKey.enabled == True).to_list()  # noqa: E712
        if d.managed_by is None
    ]
    config_prehashes = {**file_prehashes(), **provider.config_key_prehashes()}
    runtime = [(d.name, d.key_hash) for d in docs]
    # bcrypt is slow and CPU-bound: keep it off the event loop. The cache is
    # only touched by reconcile, which runs one pass at a time.
    shadows = await asyncio.to_thread(
        compute_shadows, config_prehashes, runtime, _match_cache
    )
    hashes = {d.name: d.key_hash for d in docs}

    async with provider_lock:
        for name, config_name in shadows.items():
            if _shadowed.get(name) == config_name:
                continue
            provider.remove_key(hashes[name])
            logger.error(
                "Runtime API key '%s' has the same plaintext as config key '%s'; "
                "it is withheld from key sync and does not authenticate. The "
                "config key is the one in use. Revoke '%s', or give the config "
                "key a new value.",
                name, config_name, name,
            )
        for name in [n for n in _shadowed if n not in shadows]:
            doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
            if doc is not None and doc.enabled:
                present = any(k.key_hash == doc.key_hash for k in provider.iter_keys())
                if not present:
                    provider.add_key(runtime_record(doc))
            logger.info("Runtime API key '%s' is no longer shadowed by a config key", name)
        _shadowed.clear()
        _shadowed.update(shadows)
    return dict(shadows)


async def run_reconcile_loop(provider: APIKeyProvider) -> None:
    """Reconcile every RECONCILE_INTERVAL_SECONDS until cancelled."""
    while True:
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
        try:
            await reconcile(provider)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("API key shadow check failed; keeping the previous result")


def reset_for_tests() -> None:
    _shadowed.clear()
    _match_cache.clear()
