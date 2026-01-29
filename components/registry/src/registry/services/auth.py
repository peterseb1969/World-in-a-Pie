"""Authentication service for the Registry."""

import hashlib
import hmac
import secrets
from typing import Optional

from fastapi import HTTPException, Security, Depends
from fastapi.security import APIKeyHeader


# API Key header configuration
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


class AuthService:
    """Service for API key authentication."""

    # In-memory store of valid API keys (in production, use database)
    # Maps key hash -> namespace permissions
    _api_keys: dict[str, dict] = {}

    # Master key for admin operations (set from config)
    _master_key_hash: Optional[str] = None

    @classmethod
    def initialize(cls, master_key: Optional[str] = None) -> None:
        """
        Initialize the auth service.

        Args:
            master_key: Optional master API key for admin operations
        """
        if master_key:
            cls._master_key_hash = cls.hash_api_key(master_key)

    @staticmethod
    def generate_api_key(prefix: str = "wip_sk") -> str:
        """
        Generate a new API key.

        Format: wip_sk_live_<random_bytes>

        Args:
            prefix: Key prefix (default: wip_sk)

        Returns:
            New API key string
        """
        random_part = secrets.token_urlsafe(32)
        return f"{prefix}_live_{random_part}"

    @staticmethod
    def hash_api_key(api_key: str) -> str:
        """
        Hash an API key for secure storage.

        Uses SHA-256 with a prefix salt.

        Args:
            api_key: The API key to hash

        Returns:
            Hashed key string
        """
        # Use a constant salt prefix (in production, use proper key derivation)
        salted = f"wip_registry_salt:{api_key}"
        return hashlib.sha256(salted.encode()).hexdigest()

    @classmethod
    def register_api_key(
        cls,
        api_key: str,
        namespaces: Optional[list[str]] = None,
        is_admin: bool = False,
        description: str = ""
    ) -> str:
        """
        Register an API key with permissions.

        Args:
            api_key: The API key to register
            namespaces: List of namespaces this key can access (None = all)
            is_admin: Whether this key has admin privileges
            description: Description of what this key is for

        Returns:
            The key hash for reference
        """
        key_hash = cls.hash_api_key(api_key)
        cls._api_keys[key_hash] = {
            "namespaces": namespaces,  # None means all namespaces
            "is_admin": is_admin,
            "description": description,
        }
        return key_hash

    @classmethod
    def revoke_api_key(cls, api_key: str) -> bool:
        """
        Revoke an API key.

        Args:
            api_key: The API key to revoke

        Returns:
            True if key was found and revoked, False otherwise
        """
        key_hash = cls.hash_api_key(api_key)
        if key_hash in cls._api_keys:
            del cls._api_keys[key_hash]
            return True
        return False

    @classmethod
    def validate_api_key(
        cls,
        api_key: str,
        required_namespace: Optional[str] = None,
        require_admin: bool = False
    ) -> bool:
        """
        Validate an API key and check permissions.

        Args:
            api_key: The API key to validate
            required_namespace: Optional namespace that must be accessible
            require_admin: Whether admin privileges are required

        Returns:
            True if valid and authorized, False otherwise
        """
        if not api_key:
            return False

        key_hash = cls.hash_api_key(api_key)

        # Check master key first
        if cls._master_key_hash and key_hash == cls._master_key_hash:
            return True

        # Check registered keys
        if key_hash not in cls._api_keys:
            return False

        key_info = cls._api_keys[key_hash]

        # Check admin requirement
        if require_admin and not key_info.get("is_admin", False):
            return False

        # Check namespace permission
        if required_namespace:
            allowed_namespaces = key_info.get("namespaces")
            if allowed_namespaces is not None and required_namespace not in allowed_namespaces:
                return False

        return True

    @classmethod
    def get_key_permissions(cls, api_key: str) -> Optional[dict]:
        """
        Get permissions for an API key.

        Args:
            api_key: The API key

        Returns:
            Permission dict or None if key not found
        """
        key_hash = cls.hash_api_key(api_key)

        # Master key has all permissions
        if cls._master_key_hash and key_hash == cls._master_key_hash:
            return {"namespaces": None, "is_admin": True, "description": "Master key"}

        return cls._api_keys.get(key_hash)


# Dependency for requiring API key authentication
async def require_api_key(
    api_key: Optional[str] = Security(API_KEY_HEADER)
) -> str:
    """
    FastAPI dependency that requires a valid API key.

    Raises:
        HTTPException: If no key provided or key is invalid
    """
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Provide X-API-Key header."
        )

    if not AuthService.validate_api_key(api_key):
        raise HTTPException(
            status_code=403,
            detail="Invalid API key"
        )

    return api_key


async def require_admin_key(
    api_key: Optional[str] = Security(API_KEY_HEADER)
) -> str:
    """
    FastAPI dependency that requires an admin API key.

    Raises:
        HTTPException: If no key provided or key lacks admin privileges
    """
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Provide X-API-Key header."
        )

    if not AuthService.validate_api_key(api_key, require_admin=True):
        raise HTTPException(
            status_code=403,
            detail="Admin privileges required"
        )

    return api_key


def require_namespace_access(namespace: str):
    """
    Factory for creating a dependency that checks namespace access.

    Args:
        namespace: The namespace to check access for

    Returns:
        Dependency function
    """
    async def _check_access(
        api_key: Optional[str] = Security(API_KEY_HEADER)
    ) -> str:
        if not api_key:
            raise HTTPException(
                status_code=401,
                detail="API key required. Provide X-API-Key header."
            )

        if not AuthService.validate_api_key(api_key, required_namespace=namespace):
            raise HTTPException(
                status_code=403,
                detail=f"Access denied for namespace: {namespace}"
            )

        return api_key

    return _check_access


# Optional auth - doesn't fail if no key, but validates if present
async def optional_api_key(
    api_key: Optional[str] = Security(API_KEY_HEADER)
) -> Optional[str]:
    """
    FastAPI dependency for optional API key authentication.

    Returns None if no key provided, validates if key is present.
    """
    if api_key and not AuthService.validate_api_key(api_key):
        raise HTTPException(
            status_code=403,
            detail="Invalid API key"
        )

    return api_key
