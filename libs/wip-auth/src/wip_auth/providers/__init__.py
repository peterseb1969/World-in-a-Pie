"""Authentication providers for WIP.

This module contains the auth provider implementations:
- NoAuthProvider: Pass-through for development/testing
- APIKeyProvider: Service-to-service authentication via X-API-Key header
- OIDCProvider: JWT/OIDC authentication for user sessions
"""

from .api_key import APIKeyProvider, hash_api_key, verify_api_key
from .base import AuthProvider
from .none import NoAuthProvider
from .oidc import OIDCProvider

__all__ = [
    "APIKeyProvider",
    "AuthProvider",
    "NoAuthProvider",
    "OIDCProvider",
    "hash_api_key",
    "verify_api_key",
]
