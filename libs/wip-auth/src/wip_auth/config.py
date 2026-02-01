"""Configuration for WIP authentication.

The auth configuration is loaded from environment variables with the WIP_AUTH_ prefix.
All settings can be overridden programmatically if needed.
"""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import APIKeyRecord


class AuthConfig(BaseSettings):
    """Authentication configuration.

    Loaded from environment variables with WIP_AUTH_ prefix.

    Examples:
        # API key only (default, backward compatible)
        WIP_AUTH_MODE=api_key_only
        WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing

        # Dual mode (API keys + JWT)
        WIP_AUTH_MODE=dual
        WIP_AUTH_JWT_ISSUER_URL=http://authelia:9091
        WIP_AUTH_JWT_AUDIENCE=wip
        WIP_AUTH_LEGACY_API_KEY=dev_master_key_for_testing

        # No auth (development/testing)
        WIP_AUTH_MODE=none
    """

    model_config = SettingsConfigDict(
        env_prefix="WIP_AUTH_",
        env_file=".env",
        extra="ignore",
    )

    # Core auth mode
    mode: Literal["none", "api_key_only", "jwt_only", "dual"] = Field(
        default="api_key_only",
        description="Authentication mode: none (dev), api_key_only (default), jwt_only, or dual"
    )

    # JWT/OIDC settings (used when mode is jwt_only or dual)
    jwt_provider: str = Field(
        default="generic_oidc",
        description="OIDC provider: authelia, authentik, zitadel, or generic_oidc"
    )
    jwt_issuer_url: str | None = Field(
        default=None,
        description="OIDC issuer URL (e.g., http://authelia:9091)"
    )
    jwt_jwks_uri: str | None = Field(
        default=None,
        description="JWKS endpoint URL (defaults to {issuer_url}/.well-known/jwks.json)"
    )
    jwt_audience: str = Field(
        default="wip",
        description="Expected JWT audience claim"
    )
    jwt_groups_claim: str = Field(
        default="groups",
        description="JWT claim containing user groups"
    )
    jwt_algorithms: list[str] = Field(
        default=["RS256", "ES256"],
        description="Allowed JWT signing algorithms"
    )
    jwt_verify_exp: bool = Field(
        default=True,
        description="Whether to verify JWT expiration"
    )
    jwt_leeway_seconds: int = Field(
        default=30,
        description="Clock skew tolerance in seconds for JWT validation"
    )

    # API key settings
    legacy_api_key: str | None = Field(
        default=None,
        description="Single API key for backward compatibility (maps to 'legacy' key)"
    )
    api_keys_json: str | None = Field(
        default=None,
        description="JSON array of API key definitions (alternative to file)"
    )
    api_keys_file: str | None = Field(
        default=None,
        description="Path to JSON file containing API key definitions"
    )
    api_key_header: str = Field(
        default="X-API-Key",
        description="HTTP header name for API key authentication"
    )
    api_key_hash_salt: str = Field(
        default="wip_auth_salt",
        description="Salt used when hashing API keys"
    )

    # Groups
    default_groups: list[str] = Field(
        default_factory=lambda: ["wip-users"],
        description="Default groups assigned to authenticated users without explicit groups"
    )
    admin_groups: list[str] = Field(
        default_factory=lambda: ["wip-admins"],
        description="Groups considered admin (for require_admin dependency)"
    )

    @field_validator("jwt_algorithms", mode="before")
    @classmethod
    def parse_algorithms(cls, v):
        """Parse algorithms from comma-separated string or list."""
        if isinstance(v, str):
            return [a.strip() for a in v.split(",")]
        return v

    @field_validator("default_groups", "admin_groups", mode="before")
    @classmethod
    def parse_groups(cls, v):
        """Parse groups from comma-separated string or list."""
        if isinstance(v, str):
            return [g.strip() for g in v.split(",")]
        return v

    @property
    def jwks_url(self) -> str | None:
        """Get the JWKS URL, either explicit or derived from issuer."""
        if self.jwt_jwks_uri:
            return self.jwt_jwks_uri
        if self.jwt_issuer_url:
            # Standard OIDC JWKS endpoint
            base = self.jwt_issuer_url.rstrip("/")
            return f"{base}/.well-known/jwks.json"
        return None

    @property
    def requires_jwt(self) -> bool:
        """Check if JWT validation is needed based on mode."""
        return self.mode in ("jwt_only", "dual")

    @property
    def requires_api_key(self) -> bool:
        """Check if API key validation is needed based on mode."""
        return self.mode in ("api_key_only", "dual")

    def load_api_keys(self) -> list[APIKeyRecord]:
        """Load API key records from configuration.

        Returns keys from legacy_api_key, api_keys_json, and api_keys_file.

        API keys can be defined in three ways:
        1. WIP_AUTH_LEGACY_API_KEY - Single key for backward compatibility
        2. WIP_AUTH_API_KEYS_JSON - JSON array of key definitions
        3. WIP_AUTH_API_KEYS_FILE - Path to JSON file with key definitions

        Example JSON format for api_keys_json:
        [
            {
                "name": "admin-console",
                "key": "plaintext_key_here",
                "owner": "admin@wip.local",
                "groups": ["wip-admins"]
            },
            {
                "name": "etl-service",
                "key": "another_key_here",
                "owner": "system:etl",
                "groups": ["wip-services"]
            }
        ]

        Note: Keys in JSON can be provided as plaintext "key" (will be hashed)
        or pre-hashed "key_hash". Plaintext keys are hashed on load.
        """
        from .providers.api_key import hash_api_key

        keys: list[APIKeyRecord] = []
        seen_names: set[str] = set()

        # Load legacy key if set
        if self.legacy_api_key:
            keys.append(APIKeyRecord(
                name="legacy",
                key_hash=hash_api_key(self.legacy_api_key, self.api_key_hash_salt),
                owner="system:legacy",
                groups=self.admin_groups,  # Legacy key gets admin access
                description="Legacy API key from WIP_AUTH_LEGACY_API_KEY"
            ))
            seen_names.add("legacy")

        # Load keys from JSON environment variable
        if self.api_keys_json:
            try:
                key_list = json.loads(self.api_keys_json)
                for key_dict in key_list:
                    key_record = self._parse_key_dict(key_dict, hash_api_key)
                    if key_record.name in seen_names:
                        # Skip duplicates (later sources override earlier)
                        keys = [k for k in keys if k.name != key_record.name]
                    keys.append(key_record)
                    seen_names.add(key_record.name)
            except json.JSONDecodeError as e:
                import logging
                logging.getLogger("wip_auth").warning(
                    f"Failed to parse WIP_AUTH_API_KEYS_JSON: {e}"
                )

        # Load keys from file if specified
        if self.api_keys_file:
            file_path = Path(self.api_keys_file)
            if file_path.exists():
                with open(file_path) as f:
                    key_data = json.load(f)
                for key_dict in key_data.get("keys", []):
                    key_record = self._parse_key_dict(key_dict, hash_api_key)
                    if key_record.name in seen_names:
                        keys = [k for k in keys if k.name != key_record.name]
                    keys.append(key_record)
                    seen_names.add(key_record.name)

        return keys

    def _parse_key_dict(self, key_dict: dict, hash_func) -> "APIKeyRecord":
        """Parse a key dictionary, hashing plaintext keys if needed.

        Args:
            key_dict: Dictionary with key definition
            hash_func: Function to hash plaintext keys

        Returns:
            APIKeyRecord with hashed key
        """
        # If plaintext key is provided, hash it
        if "key" in key_dict and "key_hash" not in key_dict:
            key_dict = key_dict.copy()
            key_dict["key_hash"] = hash_func(key_dict.pop("key"), self.api_key_hash_salt)

        return APIKeyRecord(**key_dict)


def get_auth_config() -> AuthConfig:
    """Get the auth configuration singleton.

    Loads from environment on first call, caches thereafter.
    """
    global _auth_config
    if _auth_config is None:
        _auth_config = AuthConfig()
    return _auth_config


def set_auth_config(config: AuthConfig) -> None:
    """Set the auth configuration (for testing)."""
    global _auth_config
    _auth_config = config


def reset_auth_config() -> None:
    """Reset the auth configuration singleton (for testing)."""
    global _auth_config
    _auth_config = None


# Module-level singleton
_auth_config: AuthConfig | None = None


# Also support loading from legacy env vars for backward compatibility
def _check_legacy_env_vars() -> None:
    """Map legacy environment variables to new names.

    For backward compatibility with existing services that use:
    - API_KEY -> WIP_AUTH_LEGACY_API_KEY
    - MASTER_API_KEY -> WIP_AUTH_LEGACY_API_KEY
    """
    legacy_mappings = [
        ("API_KEY", "WIP_AUTH_LEGACY_API_KEY"),
        ("MASTER_API_KEY", "WIP_AUTH_LEGACY_API_KEY"),
    ]

    for old_var, new_var in legacy_mappings:
        if old_var in os.environ and new_var not in os.environ:
            os.environ[new_var] = os.environ[old_var]


# Run on module import
_check_legacy_env_vars()
