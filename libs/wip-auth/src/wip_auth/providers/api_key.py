"""API key authentication provider.

Uses bcrypt for key hashing (GPU-resistant, ~1ms per verification).
Legacy SHA-256 hashes are detected and verified with a deprecation warning.
"""

import hashlib
import hmac
import logging
from datetime import UTC, datetime

import bcrypt
from fastapi import HTTPException, Request

from ..models import APIKeyRecord, UserIdentity

logger = logging.getLogger("wip_auth.api_key")


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

        # Cache: SHA-256(plaintext_key) → APIKeyRecord after first bcrypt verify.
        # Avoids re-running bcrypt (~3ms) on every request for the same key.
        self._verified_cache: dict[str, APIKeyRecord] = {}

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
        """Add a new API key at runtime."""
        if key.enabled:
            self._keys.append(key)
            self._verified_cache.clear()

    def remove_key(self, key_hash: str) -> bool:
        """Remove an API key by its hash."""
        for i, key in enumerate(self._keys):
            if key.key_hash == key_hash:
                self._keys.pop(i)
                self._verified_cache.clear()
                return True
        return False

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

        # Slow path: bcrypt verify against all registered keys
        for record in self._keys:
            if verify_api_key(api_key, record.key_hash, self.hash_salt):
                if record.is_expired():
                    return None, "API key has expired"
                self._verified_cache[fingerprint] = record
                return record, None

        return None, "Invalid API key"

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

        # Build identity from key record
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

        return namespace in namespaces
