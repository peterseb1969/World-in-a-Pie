"""API key authentication provider.

Uses bcrypt for key hashing (GPU-resistant). Verification is deliberately
slow — about 170 ms per check at bcrypt's default cost (12), measured on a dev
machine — so a key is matched against the stored hashes once and the result is
cached, and recently rejected keys are cached too.
Legacy SHA-256 hashes are detected and verified with a deprecation warning.
"""

import hashlib
import hmac
import logging
import threading
import time
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


def _is_bcrypt_hash(h: str) -> bool:
    """Check if a hash string looks like a bcrypt hash."""
    return h.startswith(("$2b$", "$2a$", "$2y$"))


def _is_sha256_hash(h: str) -> bool:
    """Check if a hash string looks like a hex-encoded SHA-256 hash."""
    return len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def hash_api_key(key: str, salt: str = "wip_auth_salt") -> str:
    """Hash an API key using bcrypt.

    The deployment salt is prefixed to the key before bcrypt hashing,
    providing defense in depth (different deployments produce different
    hashes even for the same key).

    Args:
        key: The plain-text API key
        salt: Deployment-specific salt prefix

    Returns:
        Bcrypt hash string (starts with $2b$)
    """
    # bcrypt has a 72-byte input limit. Pre-hash with SHA-256 to support
    # arbitrarily long salt+key combinations while preserving full entropy.
    salted = f"{salt}:{key}"
    prehash = hashlib.sha256(salted.encode()).digest()
    return bcrypt.hashpw(prehash, bcrypt.gensalt()).decode()


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
    salted = f"{salt}:{key}"

    if _is_bcrypt_hash(key_hash):
        try:
            # Pre-hash to match hash_api_key() (handles >72-byte inputs)
            prehash = hashlib.sha256(salted.encode()).digest()
            return bcrypt.checkpw(prehash, key_hash.encode())
        except (ValueError, TypeError):
            return False

    if _is_sha256_hash(key_hash):
        # Legacy SHA-256 fallback — constant-time comparison
        computed = hashlib.sha256(salted.encode()).hexdigest()
        return hmac.compare_digest(computed, key_hash)

    return False


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
        self.hash_salt = hash_salt
        self.default_groups = default_groups or []

        # Store enabled keys for iteration-based bcrypt verification
        self._keys: list[APIKeyRecord] = [k for k in keys if k.enabled]
        # The keys given here are the config keys (env and key file); keys
        # added later are runtime keys. The set moves with every config
        # reload, so the key sync never mistakes a new config key for a
        # vanished runtime key.
        self._config_names: frozenset[str] = frozenset(k.name for k in keys)
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

        # Warn about legacy SHA-256 hashes
        for key in self._keys:
            if _is_sha256_hash(key.key_hash):
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

    def config_key_names(self) -> frozenset[str]:
        """Names of the current config keys (env and key file)."""
        return self._config_names

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
            self._rebuild_caches()

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
        """
        # Fast path: check cache with SHA-256 fingerprint of plaintext key
        fingerprint = hashlib.sha256(api_key.encode()).hexdigest()
        cached = self._verified_cache.get(fingerprint)
        if cached is not None:
            if cached.is_expired():
                self._verified_cache.pop(fingerprint, None)
                return None, "API key has expired"
            return cached, None

        if self._recently_rejected(fingerprint):
            return None, "Invalid API key"

        # Slow path: bcrypt verify against all registered keys
        for record in self._keys:
            if verify_api_key(api_key, record.key_hash, self.hash_salt):
                if record.is_expired():
                    return None, "API key has expired"
                self._verified_cache[fingerprint] = record
                return record, None

        self._reject(fingerprint)
        return None, "Invalid API key"

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

    def lookup(self, api_key: str) -> APIKeyRecord | None:
        """The registered, unexpired key matching api_key, or None — through
        the same caches as authentication."""
        record, error = self._validate_key(api_key)
        return None if error is not None else record

    async def authenticate(self, request: Request) -> UserIdentity | None:
        """Authenticate the request using API key.

        Returns UserIdentity if authenticated, None if no API key header.
        Raises HTTPException if API key is present but invalid.
        """
        api_key = self._get_key_from_header(request)

        if api_key is None:
            return None

        key_record, error = self._validate_key(api_key)

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

    def identify_key(self, api_key: str) -> UserIdentity | None:
        """The identity of a raw API key, or None when it is unknown or expired.

        For a service that receives a key outside an HTTP request: the
        ingest gateway reads a producer's key from a NATS message header and
        needs to know whose it is. Neither logs nor raises — an unusable key
        is simply no identity.
        """
        key_record, error = self._validate_key(api_key)
        if error is not None or key_record is None:
            return None
        return self._identity(key_record)

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
                # Non-None only for config-file keys that declare grants —
                # the signal that flips permission resolution to local
                # (see wip_auth.permissions.resolve_permission).
                "grants": key_record.grants,
            },
        )

    def check_namespace_access(
        self, identity: UserIdentity, namespace: str
    ) -> bool:
        """Check if an API key identity has access to a namespace."""
        if identity.auth_method != "api_key":
            return True

        raw_claims = identity.raw_claims or {}
        namespaces = raw_claims.get("namespaces")

        if namespaces is None:
            return True

        # A config-declared grant extends the key's reach to that
        # namespace even if the namespaces list omits it (the deployer
        # validates grants ⊆ namespaces, but runtime stays permissive
        # for hand-written config files).
        grants = raw_claims.get("grants") or {}
        return namespace in namespaces or namespace in grants
