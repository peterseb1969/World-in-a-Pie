"""Authentication for the Def-Store API."""

import os
from typing import Optional

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

# API Key header configuration
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# API key from environment
_api_key: Optional[str] = None


def get_api_key() -> str:
    """Get the configured API key."""
    global _api_key
    if _api_key is None:
        _api_key = os.getenv("API_KEY", "dev_master_key_for_testing")
    return _api_key


def set_api_key(key: str) -> None:
    """Set the API key (for testing)."""
    global _api_key
    _api_key = key


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

    if api_key != get_api_key():
        raise HTTPException(
            status_code=403,
            detail="Invalid API key"
        )

    return api_key
