"""API key authentication provider."""

import hashlib
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from ..models import APIKeyRecord, UserIdentity


def hash_api_key(key: str, salt: str = "wip_auth_salt") -> str:
    """Hash an API key using SHA-256 with salt.

    Args:
        key: The plain-text API key
        salt: Salt to use for hashing

    Returns:
        Hex-encoded SHA-256 hash
    """
    salted = f"{salt}:{key}"
    return hashlib.sha256(salted.encode()).hexdigest()


class APIKeyProvider:
    """Authentication provider for API key authentication.

    Validates API keys from the X-API-Key header (configurable) against
    a list of registered keys. Keys are stored as hashes for security.

    Features:
    - Multiple keys with different permissions
    - Group-based authorization
    - Namespace restrictions (optional)
    - Key usage tracking

    Example:
        from wip_auth import hash_api_key, APIKeyRecord, APIKeyProvider

        keys = [
            APIKeyRecord(
                name="service-key",
                key_hash=hash_api_key("my_secret_key"),
                groups=["wip-services"],
            )
        ]
        provider = APIKeyProvider(keys)
        identity = await provider.authenticate(request)
    """

    def __init__(
        self,
        keys: list[APIKeyRecord],
        header_name: str = "X-API-Key",
        hash_salt: str = "wip_auth_salt",
        default_groups: list[str] | None = None,
    ):
        """Initialize the API key provider.

        Args:
            keys: List of registered API key records
            header_name: HTTP header to read the key from
            hash_salt: Salt used when hashing keys for comparison
            default_groups: Groups to assign if key has no explicit groups
        """
        self.header_name = header_name
        self.hash_salt = hash_salt
        self.default_groups = default_groups or []

        # Build lookup table by hash for O(1) validation
        self._keys_by_hash: dict[str, APIKeyRecord] = {}
        for key in keys:
            if key.enabled:
                self._keys_by_hash[key.key_hash] = key

    def add_key(self, key: APIKeyRecord) -> None:
        """Add a new API key at runtime.

        Args:
            key: The API key record to add
        """
        if key.enabled:
            self._keys_by_hash[key.key_hash] = key

    def remove_key(self, key_hash: str) -> bool:
        """Remove an API key by its hash.

        Args:
            key_hash: The hash of the key to remove

        Returns:
            True if key was found and removed, False otherwise
        """
        if key_hash in self._keys_by_hash:
            del self._keys_by_hash[key_hash]
            return True
        return False

    def _get_key_from_header(self, request: Request) -> str | None:
        """Extract API key from request header.

        Args:
            request: The FastAPI request

        Returns:
            The API key string or None if not present
        """
        return request.headers.get(self.header_name)

    def _validate_key(self, api_key: str) -> APIKeyRecord | None:
        """Validate an API key and return its record if valid.

        Args:
            api_key: The plain-text API key

        Returns:
            The APIKeyRecord if valid, None otherwise
        """
        key_hash = hash_api_key(api_key, self.hash_salt)
        return self._keys_by_hash.get(key_hash)

    async def authenticate(self, request: Request) -> UserIdentity | None:
        """Authenticate the request using API key.

        Args:
            request: The FastAPI request

        Returns:
            UserIdentity if authenticated, None if no API key header

        Raises:
            HTTPException: If API key is present but invalid
        """
        api_key = self._get_key_from_header(request)

        if api_key is None:
            # No API key header - let other providers try
            return None

        key_record = self._validate_key(api_key)

        if key_record is None:
            raise HTTPException(
                status_code=401,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        # Update last used timestamp (in-memory only)
        key_record.last_used_at = datetime.now(timezone.utc)

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
        """Check if an API key identity has access to a namespace.

        Args:
            identity: The authenticated identity
            namespace: The namespace to check

        Returns:
            True if access is allowed, False otherwise
        """
        if identity.auth_method != "api_key":
            # Non-API key identities have no namespace restrictions
            return True

        raw_claims = identity.raw_claims or {}
        namespaces = raw_claims.get("namespaces")

        if namespaces is None:
            # No namespace restrictions
            return True

        return namespace in namespaces
