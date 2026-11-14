"""API key authentication provider.

Uses bcrypt for key hashing (GPU-resistant). Verification is deliberately
slow — about 170 ms per check at bcrypt's default cost (12), measured on a dev
machine — so a key is matched against the stored hashes once and the result is
cached, and recently rejected keys are cached too.
Legacy SHA-256 hashes are still verified; each key carrying one is logged
with a deprecation warning once, when the provider first holds it (at
construction, a key-file reload, a key sync or an added key).
"""

import asyncio
import hashlib
import hmac
import logging
import threading
import time
import weakref
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

import bcrypt
from fastapi import HTTPException, Request

from ..models import APIKeyRecord, UserIdentity

logger = logging.getLogger("wip_auth.api_key")

# Recently rejected keys, so a repeated wrong key costs one dictionary lookup
# instead of a bcrypt check against every stored hash. Bounded and short-lived;
# cleared whenever a key is added, since a rejected key may have just become
# valid.
_REJECTED_MAX = 1024
_REJECTED_TTL_SECONDS = 60.0
# Slow-path checks (bcrypt against every stored key) running at once, per
# event loop. Each holds a worker thread for N x ~170 ms; two keep a first-time
# key's check moving without letting a flood of unknown values take every
# thread the loop's executor has.
_SLOW_PATH_CONCURRENCY = 2


def _monotonic() -> float:
    """The clock the runtime-key deadline is measured on (patched in tests)."""
    return time.monotonic()


class KeysUnverifiable(Exception):
    """The presented key can only be a runtime key, and the runtime keys are
    past their deadline: the Registry has not confirmed them recently enough
    to answer for them. Authentication reports it as 503, not 401."""


def _is_bcrypt_hash(h: str) -> bool:
    """Check if a hash string looks like a bcrypt hash."""
    return h.startswith(("$2b$", "$2a$", "$2y$"))


def _is_sha256_hash(h: str) -> bool:
    """Check if a hash string looks like a hex-encoded SHA-256 hash."""
    return len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def api_key_prehash(key: str, salt: str = "wip_auth_salt") -> bytes:
    """SHA-256 of the salted key: what bcrypt hashes, and what a legacy
    SHA-256 hash is the hex of.

    bcrypt has a 72-byte input limit, so the salt and key are pre-hashed to
    support arbitrarily long combinations while preserving full entropy.
    """
    return hashlib.sha256(f"{salt}:{key}".encode()).digest()


def hash_api_key(key: str, salt: str = "wip_auth_salt") -> str:
    """Hash an API key using bcrypt.

    The salt is prefixed to the key before bcrypt hashing. Runtime keys use
    the salt the Registry keeps for the stack (generated per install, or the
    old default `wip_auth_salt` on an install that had keys before the
    Registry kept one); config keys use the process's
    WIP_AUTH_API_KEY_HASH_SALT. Two hashes of one key under one salt differ
    only through bcrypt's own per-hash salt.

    Args:
        key: The plain-text API key
        salt: Deployment-specific salt prefix

    Returns:
        Bcrypt hash string (starts with $2b$)
    """
    return bcrypt.hashpw(api_key_prehash(key, salt), bcrypt.gensalt()).decode()


def prehash_matches(prehash: bytes, key_hash: str) -> bool:
    """Whether a key whose api_key_prehash is `prehash` matches `key_hash`.

    Supports both bcrypt (preferred) and legacy SHA-256 hashes, with
    constant-time comparison for the latter.
    """
    if _is_bcrypt_hash(key_hash):
        try:
            return bcrypt.checkpw(prehash, key_hash.encode())
        except (ValueError, TypeError):
            return False

    if _is_sha256_hash(key_hash):
        return hmac.compare_digest(prehash.hex(), key_hash)

    return False


def verify_api_key(key: str, key_hash: str, salt: str = "wip_auth_salt") -> bool:
    """Verify a plaintext API key against its stored hash.

    Supports both bcrypt (preferred) and legacy SHA-256 hashes.
    Uses constant-time comparison to prevent timing attacks.

    Args:
        key: The plain-text API key
        key_hash: The stored hash (bcrypt or SHA-256)
        salt: Deployment-specific salt prefix

    Returns:
        True if the key matches the hash
    """
    return prehash_matches(api_key_prehash(key, salt), key_hash)


class APIKeyProvider:
    """Authentication provider for API key authentication.

    Validates API keys from the X-API-Key header (configurable) against
    a list of registered keys. Keys are stored as bcrypt hashes.

    Features:
    - Multiple keys with different permissions
    - Group-based authorization
    - Namespace restrictions (optional)
    - Key usage tracking (structured logging)
    """

    def __init__(
        self,
        keys: list[APIKeyRecord],
        header_name: str = "X-API-Key",
        hash_salt: str = "wip_auth_salt",
        default_groups: list[str] | None = None,
    ):
        self.header_name = header_name
        # Two salts. Config keys are hashed and verified inside this process,
        # so their salt (hash_salt, from WIP_AUTH_API_KEY_HASH_SALT) only has
        # to agree with itself. Runtime keys are the Registry's records, all
        # hashed under the one salt the Registry keeps for the stack; key
        # sync hands it over with the keys (set_runtime_salt). Until then the
        # runtime salt is the configured one, which is what every stack used
        # before the Registry kept its own.
        self.hash_salt = hash_salt
        self.runtime_salt = hash_salt
        self.default_groups = default_groups or []

        # Store enabled keys for iteration-based bcrypt verification
        self._keys: list[APIKeyRecord] = [k for k in keys if k.enabled]
        # The keys given here are the config keys (env and key file); keys
        # added later are runtime keys. The set moves with every config
        # reload, so the key sync never mistakes a new config key for a
        # vanished runtime key.
        self._config_names: frozenset[str] = frozenset(k.name for k in keys)
        # Every config record, a disabled one included: it authenticates
        # nothing, but a key listing must still show it.
        self._config_records: tuple[APIKeyRecord, ...] = tuple(keys)
        # Writers run on the event loop (key sync, runtime key API) and on
        # the key-file watcher thread; readers take the current references.
        self._lock = threading.Lock()
        # Set by create_providers_from_config when the key file is watched.
        self.file_watcher: Any = None

        # Cache: SHA-256(plaintext_key) → APIKeyRecord after first bcrypt verify.
        # Avoids re-running bcrypt (~170 ms per stored hash) on every request.
        self._verified_cache: dict[str, APIKeyRecord] = {}
        # SHA-256(plaintext_key) → monotonic expiry, for keys matching nothing.
        self._rejected: OrderedDict[str, float] = OrderedDict()
        # The slow path (bcrypt against every stored key) runs in a worker
        # thread, at most _SLOW_PATH_CONCURRENCY at a time per event loop,
        # so a flood of unknown values never blocks the loop. One semaphore
        # per loop: an asyncio.Semaphore binds to the loop it first waits on.
        self._slow_path: weakref.WeakKeyDictionary[Any, asyncio.Semaphore] = (
            weakref.WeakKeyDictionary()
        )

        # Runtime keys are the Registry's keys, copied here by key sync; the
        # copy is a cache and answers only until this monotonic deadline.
        # None means no deadline: a provider nobody syncs (the Registry's
        # own, whose keys are not a copy) never expires them.
        self._runtime_deadline: float | None = None
        self._runtime_ttl: float | None = None

        # The legacy SHA-256 hashes already warned about, so a key sync
        # every 30 s does not repeat the warning.
        self._warned_legacy: set[str] = set()
        self._warn_legacy_hashes(self._keys)

    def _warn_legacy_hashes(self, keys: list[APIKeyRecord]) -> None:
        """Log each key that carries a legacy SHA-256 hash, once per hash."""
        for key in keys:
            if _is_sha256_hash(key.key_hash) and key.key_hash not in self._warned_legacy:
                self._warned_legacy.add(key.key_hash)
                logger.warning(
                    "API key '%s' uses legacy SHA-256 hash. "
                    "Re-generate with bcrypt for better security: "
                    "python -c \"from wip_auth import hash_api_key; print(hash_api_key('YOUR_KEY'))\"",
                    key.name,
                )

    def add_key(self, key: APIKeyRecord) -> None:
        """Add a new API key at runtime.

        Verified entries stay valid — another key's arrival changes nothing
        about them. Rejections are forgotten: one may be this new key.
        """
        if key.enabled:
            with self._lock:
                self._keys = [*self._keys, key]
                self._rejected.clear()
                self._warn_legacy_hashes([key])

    def remove_key(self, key_hash: str) -> bool:
        """Remove an API key by its hash, evicting only that key's cache entry."""
        with self._lock:
            for i, key in enumerate(self._keys):
                if key.key_hash == key_hash:
                    self._keys = self._keys[:i] + self._keys[i + 1:]
                    self._verified_cache = {
                        fp: rec for fp, rec in self._verified_cache.items()
                        if rec.key_hash != key_hash
                    }
                    return True
            return False

    def set_runtime_salt(self, salt: str) -> None:
        """Verify runtime keys under `salt` from now on.

        A change forgets every verified runtime key and every rejection: a
        match made under the old salt says nothing under the new one. Config
        keys keep their own salt and their cached matches.
        """
        with self._lock:
            if salt == self.runtime_salt:
                return
            self.runtime_salt = salt
            names = self._config_names
            self._verified_cache = {
                fp: rec for fp, rec in self._verified_cache.items() if rec.name in names
            }
            self._rejected.clear()

    def config_records(self) -> tuple[APIKeyRecord, ...]:
        """Every config key's record, enabled or not, as last loaded."""
        return self._config_records

    def config_key_names(self) -> frozenset[str]:
        """Names of the current config keys (env and key file)."""
        return self._config_names

    def config_key_prehashes(self, salt: str | None = None) -> dict[str, bytes]:
        """api_key_prehash of each loaded, enabled config key that was given
        as a plaintext, by name. Pre-hashed config entries have none.

        Under the config salt by default; pass `salt` to get the prehashes a
        comparison with hashes made under another salt (the runtime keys')
        needs.
        """
        keys, names = self._keys, self._config_names
        result: dict[str, bytes] = {}
        for k in keys:
            if k.name not in names:
                continue
            prehash = k.plaintext_prehash if salt is None else k.plaintext_prehash_under(salt)
            if prehash is not None:
                result[k.name] = prehash
        return result

    def reload_config_keys(self, config_keys: list["APIKeyRecord"]) -> None:
        """Replace the config keys atomically, keeping the runtime keys.

        The config-key names move in the same swap as the records: the key
        sync preserves config keys by name, so a key added here must count as
        a config key from the moment it exists, or the next sync drops it.
        A runtime key whose name a config key now takes is dropped.
        """
        names = frozenset(k.name for k in config_keys)
        with self._lock:
            runtime = [
                k for k in self._keys
                if k.name not in self._config_names and k.name not in names
            ]
            self._keys = [k for k in config_keys if k.enabled] + runtime
            self._config_names = names
            self._config_records = tuple(config_keys)
            self._rebuild_caches()
            self._warn_legacy_hashes(self._keys)

    def _rebuild_caches(self) -> None:
        current = {k.key_hash: k for k in self._keys}
        self._verified_cache = {
            fp: current[rec.key_hash]
            for fp, rec in self._verified_cache.items()
            if rec.key_hash in current
        }
        self._rejected.clear()

    def replace_runtime_keys(
        self,
        runtime_keys: list["APIKeyRecord"],
        config_key_names: set[str] | None = None,
    ) -> None:
        """Replace all runtime keys atomically. Config keys are untouched.

        Runs on every key-sync poll (every 30 s in every service), so it keeps
        the verified entries of keys that still exist — re-pointed at the
        fresh records, whose groups or grants may have changed — and drops
        only those of keys that were removed or disabled.

        Config keys are recognised by the provider's own current name set,
        which a key-file reload keeps current. Passing `config_key_names`
        declares that set instead; callers must not pass a startup snapshot,
        since after a reload it is stale and would drop a new config key.
        """
        with self._lock:
            if config_key_names is not None:
                self._config_names = frozenset(config_key_names)
            config_keys = [k for k in self._keys if k.name in self._config_names]
            runtime = [
                k for k in runtime_keys if k.enabled and k.name not in self._config_names
            ]
            self._keys = config_keys + runtime
            self._rebuild_caches()
            self._warn_legacy_hashes(runtime)

    def expire_runtime_keys(self) -> None:
        """Mark the runtime keys unconfirmed until the next successful sync.

        Key sync calls this when it starts, so a synced provider does not
        answer for runtime keys before the Registry has confirmed them once.
        """
        self._runtime_deadline = _monotonic()

    def confirm_runtime_keys(self, ttl_seconds: float) -> None:
        """The Registry just confirmed the runtime keys: they answer for
        ttl_seconds more. Only a successful sync calls this."""
        self._runtime_ttl = ttl_seconds
        self._runtime_deadline = _monotonic() + ttl_seconds

    def _invalid_message(self) -> str:
        """The refusal of a key this process does not know. Where the runtime
        keys are a synced copy of the Registry's, a key the Registry created
        or changed a moment ago may simply not have arrived yet, and the
        message says so: "invalid" alone reads as "this key is wrong"."""
        if self._runtime_ttl is None:
            return "Invalid API key"
        return (
            "Invalid API key (a key created or changed in the last 30 seconds "
            "may not have reached this service yet; retry shortly)"
        )

    def runtime_keys_expired(self) -> bool:
        """Whether the runtime keys are past their deadline."""
        deadline = self._runtime_deadline
        return deadline is not None and _monotonic() >= deadline

    def _unverifiable(self) -> KeysUnverifiable:
        if self._runtime_ttl is None:
            reason = "the Registry has not confirmed them since this service started"
        else:
            reason = f"the Registry has not confirmed them within the last {self._runtime_ttl:g} s"
        return KeysUnverifiable(f"API keys cannot be verified: {reason}")

    def iter_keys(self) -> tuple[APIKeyRecord, ...]:
        """Read-only snapshot of the registered keys.

        Public accessor so callers (e.g. the Registry's key-listing and
        /sync endpoints) iterate keys without reaching into the private
        `_keys` list and coupling to its representation.
        """
        return tuple(self._keys)

    def _get_key_from_header(self, request: Request) -> str | None:
        """Extract API key from request header."""
        return request.headers.get(self.header_name)

    def _validate_key(self, api_key: str) -> tuple[APIKeyRecord | None, str | None]:
        """Validate an API key against all registered keys.

        Uses a fast SHA-256 fingerprint cache so bcrypt only runs once
        per unique key. Subsequent requests hit the O(1) cache lookup.

        Past the runtime-key deadline only config keys answer: a key that
        matches no config key raises KeysUnverifiable, whether it is a known
        runtime key, a cached one, or no key at all — the copy cannot say
        which, and saying "invalid" would tell a valid caller its key is bad.
        """
        expired = self.runtime_keys_expired()
        config_names = self._config_names
        config_salt, runtime_salt = self.hash_salt, self.runtime_salt

        # Fast path: check cache with SHA-256 fingerprint of plaintext key.
        # The caches are shared with the worker threads the slow path runs
        # in, so every read and write of them holds the lock; bcrypt never
        # does.
        fingerprint = hashlib.sha256(api_key.encode()).hexdigest()
        with self._lock:
            cached = self._verified_cache.get(fingerprint)
            if cached is not None and cached.is_expired():
                self._verified_cache.pop(fingerprint, None)
            rejected = cached is None and self._recently_rejected(fingerprint)
            keys = self._keys
        if cached is not None:
            if expired and cached.name not in config_names:
                raise self._unverifiable()
            if cached.is_expired():
                return None, "API key has expired"
            return cached, None

        if rejected:
            if expired:
                raise self._unverifiable()
            return None, self._invalid_message()

        # Slow path: bcrypt verify against the registered keys — only the
        # config keys once the runtime copy is past its deadline.
        for record in keys:
            is_config = record.name in config_names
            if expired and not is_config:
                continue
            salt = config_salt if is_config else runtime_salt
            if verify_api_key(api_key, record.key_hash, salt):
                if record.is_expired():
                    return None, "API key has expired"
                with self._lock:
                    # Cache only a match against the keys as they still are:
                    # a reload or sync that landed while bcrypt ran may have
                    # removed this very key and rebuilt the cache without
                    # it, and caching it now would let it authenticate until
                    # the next rebuild. Every change replaces the list (or
                    # the salt), so an unchanged list is an exact test.
                    current = self._keys is keys and self.runtime_salt == runtime_salt
                    if current:
                        self._verified_cache[fingerprint] = record
                if not current:
                    return self._validate_key(api_key)
                return record, None

        # Matching no config key is true whatever the Registry says, so the
        # rejection is cached either way; the next successful sync clears it.
        # Unless the keys changed while bcrypt ran: a sync that landed
        # meanwhile may have brought this very key, and recording the
        # rejection after it cleared the cache would refuse the new key until
        # the next sync. Every change replaces the list (or the salt), so an
        # unchanged list is an exact test.
        with self._lock:
            if self._keys is keys and self.runtime_salt == runtime_salt:
                self._reject(fingerprint)
        if expired:
            raise self._unverifiable()
        return None, self._invalid_message()

    def _recently_rejected(self, fingerprint: str) -> bool:
        expiry = self._rejected.get(fingerprint)
        if expiry is None:
            return False
        if expiry < time.monotonic():
            del self._rejected[fingerprint]
            return False
        return True

    def _reject(self, fingerprint: str) -> None:
        self._rejected[fingerprint] = time.monotonic() + _REJECTED_TTL_SECONDS
        self._rejected.move_to_end(fingerprint)
        while len(self._rejected) > _REJECTED_MAX:
            self._rejected.popitem(last=False)

    def _known_to_caches(self, api_key: str) -> bool:
        """Whether a value is answered without bcrypt: it verified before, or
        was rejected recently."""
        fingerprint = hashlib.sha256(api_key.encode()).hexdigest()
        with self._lock:
            return fingerprint in self._verified_cache or self._recently_rejected(fingerprint)

    async def _validate_key_async(
        self, api_key: str
    ) -> tuple[APIKeyRecord | None, str | None]:
        """_validate_key without blocking the event loop: a value the caches
        know is answered inline; any other runs bcrypt in a worker thread,
        behind the per-loop semaphore."""
        if self._known_to_caches(api_key):
            return self._validate_key(api_key)
        loop = asyncio.get_running_loop()
        semaphore = self._slow_path.get(loop)
        if semaphore is None:
            semaphore = self._slow_path[loop] = asyncio.Semaphore(_SLOW_PATH_CONCURRENCY)
        async with semaphore:
            return await asyncio.to_thread(self._validate_key, api_key)

    def lookup(
        self, api_key: str, *, raise_unverifiable: bool = False
    ) -> APIKeyRecord | None:
        """The registered, unexpired key matching api_key, or None — through
        the same caches as authentication. None too when the key cannot be
        verified (runtime keys past their deadline), unless
        raise_unverifiable=True, which raises KeysUnverifiable instead for
        a caller that must tell the two apart.

        Runs bcrypt on the calling thread: never call it on an event loop
        (an unknown value would hold the loop for N x ~170 ms); use
        lookup_async there."""
        try:
            record, error = self._validate_key(api_key)
        except KeysUnverifiable:
            if raise_unverifiable:
                raise
            return None
        return None if error is not None else record

    async def lookup_async(
        self, api_key: str, *, raise_unverifiable: bool = False
    ) -> APIKeyRecord | None:
        """lookup for a caller on the event loop: an unknown value's bcrypt
        checks run in a worker thread, behind the same per-loop semaphore as
        authentication, instead of holding the loop for N x ~170 ms."""
        try:
            record, error = await self._validate_key_async(api_key)
        except KeysUnverifiable:
            if raise_unverifiable:
                raise
            return None
        return None if error is not None else record

    async def authenticate(self, request: Request) -> UserIdentity | None:
        """Authenticate the request using API key.

        Returns UserIdentity if authenticated, None if no API key header.
        Raises HTTPException if API key is present but invalid (401), or
        cannot be verified because the runtime keys are past their deadline
        (503 — the Registry is unreachable, not the key wrong).
        """
        api_key = self._get_key_from_header(request)

        if api_key is None:
            return None

        try:
            key_record, error = await self._validate_key_async(api_key)
        except KeysUnverifiable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        if error is not None:
            raise HTTPException(
                status_code=401,
                detail=error,
                headers={"WWW-Authenticate": "ApiKey"},
            )

        assert key_record is not None  # guaranteed when error is None

        # Update last used timestamp (in-memory only)
        key_record.last_used_at = datetime.now(UTC)

        # Structured key usage log (M5 — audit trail for key rotation decisions)
        logger.info(
            "API key used: name=%s owner=%s endpoint=%s %s",
            key_record.name,
            key_record.owner or "unknown",
            request.method,
            request.url.path,
        )

        return self._identity(key_record)

    def identify_key(
        self, api_key: str, *, raise_unverifiable: bool = False
    ) -> UserIdentity | None:
        """The identity of a raw API key, or None when it is unknown or expired.

        For a service that receives a key outside an HTTP request: the
        ingest gateway reads a producer's key from a NATS message header and
        needs to know whose it is. Never logs. By default an unusable key is
        simply no identity, and so is one that cannot be verified now; with
        raise_unverifiable=True the second case raises KeysUnverifiable
        instead, for a caller that must tell "this key is wrong" from "the
        Registry has not confirmed the keys" (the latter may pass later).

        Runs bcrypt on the calling thread: on an event loop use
        identify_key_async.
        """
        try:
            key_record, error = self._validate_key(api_key)
        except KeysUnverifiable:
            if raise_unverifiable:
                raise
            return None
        if error is not None or key_record is None:
            return None
        return self._identity(key_record)

    async def identify_key_async(
        self, api_key: str, *, raise_unverifiable: bool = False
    ) -> UserIdentity | None:
        """identify_key for a caller on the event loop: an unknown value's
        bcrypt checks run off the loop, as in authentication."""
        record = await self.lookup_async(api_key, raise_unverifiable=raise_unverifiable)
        return None if record is None else self._identity(record)

    def _identity(self, key_record: APIKeyRecord) -> UserIdentity:
        """The identity an API key authenticates as."""
        groups = key_record.groups if key_record.groups else self.default_groups

        return UserIdentity(
            user_id=f"apikey:{key_record.name}",
            username=key_record.name,
            email=None,
            groups=groups,
            auth_method="api_key",
            provider="api_key",
            raw_claims={
                "key_name": key_record.name,
                "owner": key_record.owner,
                "namespaces": key_record.namespaces,
                # Non-None only for config-file keys that declare grants.
                # Only the Registry reads them (its own resolution order);
                # a service asks the Registry for every permission.
                "grants": key_record.grants,
            },
        )
