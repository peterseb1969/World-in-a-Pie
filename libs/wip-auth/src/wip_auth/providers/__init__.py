"""Authentication providers for WIP.

This module contains the auth provider implementations:
- NoAuthProvider: Pass-through for development/testing
- APIKeyProvider: Service-to-service authentication via X-API-Key header
- OIDCProvider: JWT/OIDC authentication for user sessions
- TrustedHeaderProvider: Gateway/proxy identity via X-WIP-User + API key validation
"""

from .api_key import APIKeyProvider, hash_api_key, verify_api_key
from .base import AuthProvider
from .none import NoAuthProvider
from .oidc import OIDCProvider
from .trusted_header import TrustedHeaderProvider

__all__ = [
    "APIKeyProvider",
    "AuthProvider",
    "NoAuthProvider",
    "OIDCProvider",
    "TrustedHeaderProvider",
    "hash_api_key",
    "verify_api_key",
]
