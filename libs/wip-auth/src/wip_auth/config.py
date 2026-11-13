"""Configuration for WIP authentication.

The auth configuration is loaded from environment variables with the WIP_AUTH_ prefix.
All settings can be overridden programmatically if needed.
"""

import json
import logging
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .groups import ADMIN_GROUP, PRIVILEGED_GROUPS
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
        description=(
            "Accepted JWT audience(s), comma-separated. A token is accepted "
            "when its aud matches any entry — an OIDC provider sets aud to "
            "the client that requested the token, so list every client "
            "whose tokens the service accepts."
        ),
    )
    jwt_groups_claim: str = Field(
        default="groups",
        description="JWT claim containing user groups"
    )
    federated_groups: str = Field(
        default="",
        description=(
            "Group policy for logins federated through a Dex connector, as "
            "JSON (see wip_auth.federation): reserved groups stripped, "
            "email/domain mappings applied. Rendered by the deployer when "
            "the install declares a connector; empty leaves groups as issued. "
            "Superseded by federated_groups_file when that is set."
        ),
    )
    federated_groups_file: str | None = Field(
        default=None,
        description=(
            "Path to the federated group policy as a JSON file (same format as "
            "federated_groups; an empty file means no policy). Re-read while "
            "running, so a mapping change applies without a restart."
        ),
    )
    federated_groups_file_reload_seconds: float = Field(
        default=10.0,
        description=(
            "How often to check the federated group policy file for changes; "
            "0 disables the reload"
        ),
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
    api_keys_file_reload_seconds: float = Field(
        default=10.0,
        description=(
            "How often to check the API key file for changes and apply them "
            "without a restart; 0 disables the reload"
        ),
    )
    api_key_header: str = Field(
        default="X-API-Key",
        description="HTTP header name for API key authentication"
    )
    api_key_hash_salt: str = Field(
        default="wip_auth_salt",
        description="Salt used when hashing API keys"
    )

    # Trusted proxy headers
    trust_proxy_headers: bool = Field(
        default=False,
        description="Trust X-WIP-User/X-WIP-Groups headers from proxies (requires valid API key)"
    )

    # Groups
    default_groups: list[str] = Field(
        default_factory=lambda: ["wip-users"],
        description="Default groups assigned to authenticated users without explicit groups"
    )

    @field_validator("jwt_algorithms", mode="before")
    @classmethod
    def parse_algorithms(cls, v):
        """Parse algorithms from comma-separated string or list."""
        if isinstance(v, str):
            return [a.strip() for a in v.split(",")]
        return v

    @field_validator("default_groups", mode="before")
    @classmethod
    def parse_groups(cls, v):
        """Parse groups from comma-separated string or list."""
        if isinstance(v, str):
            return [g.strip() for g in v.split(",")]
        return v

    @property
    def admin_groups(self) -> list[str]:
        """The platform administrators' group, as a list: ``[ADMIN_GROUP]``.

        Not configurable. The Registry, every store and the auth gateway
        name the group "wip-admins" themselves, so a service told another
        name would disagree with the authority it asks: its own admin check
        would stop recognising wip-admins while the Registry still granted
        them admin, and its master key would lose the group key sync
        requires. A WIP_AUTH_ADMIN_GROUPS setting is ignored.
        """
        return [ADMIN_GROUP]

    @property
    def jwt_audiences(self) -> list[str]:
        """The accepted audiences, parsed from the comma-separated setting.

        Empty is refused: an empty audience would switch audience
        verification off and accept a token minted for any client.
        """
        audiences = [a.strip() for a in self.jwt_audience.split(",") if a.strip()]
        if not audiences:
            raise ValueError(
                "WIP_AUTH_JWT_AUDIENCE is empty — list the OIDC client ids "
                "whose tokens this service accepts"
            )
        return audiences

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

        logger = logging.getLogger("wip_auth")
        keys: list[APIKeyRecord] = []
        # Plaintext per key name, for the shared-value check below. A later
        # source replaces an earlier key of the same name, here as in `keys`.
        plaintexts: dict[str, str] = {}

        if self.legacy_api_key:
            plaintexts["legacy"] = self.legacy_api_key
            keys.append(self._legacy_key_record(self.legacy_api_key))

        for key_dict in self._json_key_dicts(logger) + self._file_key_dicts():
            key_record = self._parse_key_dict(key_dict, hash_api_key)
            # Later sources override earlier: a key of the same name goes.
            keys = [k for k in keys if k.name != key_record.name]
            keys.append(key_record)
            _track_plaintext(plaintexts, key_record.name, key_dict)

        keys = _drop_shared_plaintexts(keys, plaintexts, logger)
        _warn_unscoped(keys, logger)
        _warn_declared_grants(keys, logger)
        return keys

    def _legacy_key_record(self, plaintext: str) -> APIKeyRecord:
        """The WIP_AUTH_LEGACY_API_KEY record: named "legacy", with admin
        access."""
        from .providers.api_key import api_key_prehash, hash_api_key

        legacy = APIKeyRecord(
            name="legacy",
            key_hash=hash_api_key(plaintext, self.api_key_hash_salt),
            owner="system:legacy",
            groups=self.admin_groups,  # Legacy key gets admin access
            description="Legacy API key from WIP_AUTH_LEGACY_API_KEY",
            created_at=None,
            last_used_at=None,
            expires_at=None,
            namespaces=None,
        )
        legacy._plaintext_prehash = api_key_prehash(plaintext, self.api_key_hash_salt)
        legacy._plaintext = plaintext
        return legacy

    def _json_key_dicts(self, logger: logging.Logger) -> list[dict]:
        """The key definitions in WIP_AUTH_API_KEYS_JSON; none, with a
        warning, when it does not parse."""
        if not self.api_keys_json:
            return []
        try:
            return list(json.loads(self.api_keys_json))
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse WIP_AUTH_API_KEYS_JSON: {e}")
            return []

    def _file_key_dicts(self) -> list[dict]:
        """The key definitions in WIP_AUTH_API_KEYS_FILE, if the file exists."""
        if not self.api_keys_file:
            return []
        file_path = Path(self.api_keys_file)
        if not file_path.exists():
            return []
        with open(file_path) as f:
            key_data = json.load(f)
        return list(key_data.get("keys", []))

    def _parse_key_dict(self, key_dict: dict, hash_func) -> "APIKeyRecord":
        """Parse a key dictionary, hashing plaintext keys if needed.

        Args:
            key_dict: Dictionary with key definition
            hash_func: Function to hash plaintext keys

        Returns:
            APIKeyRecord with hashed key
        """
        from .providers.api_key import api_key_prehash

        # If plaintext key is provided, hash it
        plaintext = None
        if "key" in key_dict and "key_hash" not in key_dict:
            key_dict = key_dict.copy()
            plaintext = key_dict.pop("key")
            key_dict["key_hash"] = hash_func(plaintext, self.api_key_hash_salt)

        record = APIKeyRecord(**key_dict)
        if plaintext:
            record._plaintext_prehash = api_key_prehash(plaintext, self.api_key_hash_salt)
            record._plaintext = plaintext
        return record


def _drop_shared_plaintexts(
    keys: list[APIKeyRecord], plaintexts: dict[str, str], logger: logging.Logger,
) -> list[APIKeyRecord]:
    """The keys without those whose plaintext another key shares.

    One plaintext, one key. A presented key authenticates as the first
    record it matches and that answer is cached, so a value two keys share
    is whichever key a process matched first: services started at different
    times disagree about who the caller is. Refuse the value instead — none
    of its keys load. The master key is the exception: services authenticate
    to each other with it, so it stays and the keys sharing its value are
    dropped.
    """
    dropped = _shared_plaintext_names(plaintexts)
    if not dropped:
        return keys
    for group in _shared_plaintext_groups(plaintexts):
        logger.error(
            "API keys %s share one plaintext; %s not loaded. Give each key "
            "its own value.",
            ", ".join(group),
            ", ".join(n for n in group if n in dropped),
        )
    return [k for k in keys if k.name not in dropped]



def _warn_unscoped(keys: list[APIKeyRecord], logger: logging.Logger) -> None:
    """Warn, at load time, about each key with no namespace scope outside
    the privileged groups: it will have no access."""
    for key in keys:
        if key.namespaces is None and not any(g in PRIVILEGED_GROUPS for g in key.groups):
            logger.warning(
                "API key '%s' has no namespace scope and is not in a privileged "
                "group — it will have NO access. Add 'namespaces' to the key "
                "config or assign it to wip-admins/wip-services.",
                key.name,
            )


def _warn_declared_grants(keys: list[APIKeyRecord], logger: logging.Logger) -> None:
    """Warn, at load time, about each key that declares grants: this
    loader does not apply them. Every permission is the Registry's answer,
    and the Registry takes a key's grants only from the key file it imports
    (in the Registry that can be the very file loaded here, on an install
    rendered before the key file moved, so the warning does not claim the
    grants go unread). A key the Registry has not imported has none there,
    whatever this config says."""
    for key in keys:
        if key.grants:
            logger.warning(
                "API key '%s' declares grants (%s) in this service's key "
                "config, which does not apply them. Permissions come from the "
                "Registry, which takes a key's grants only from the key file "
                "it imports — a key the Registry has not imported has no "
                "grants, whatever this config says.",
                key.name,
                ", ".join(f"{ns}={perm}" for ns, perm in sorted(key.grants.items())),
            )


def _track_plaintext(plaintexts: dict[str, str], name: str, key_dict: dict) -> None:
    """Record a key's plaintext; a pre-hashed entry has none to compare."""
    if "key" in key_dict and "key_hash" not in key_dict and key_dict["key"]:
        plaintexts[name] = key_dict["key"]
    else:
        plaintexts.pop(name, None)


def _shared_plaintext_groups(plaintexts: dict[str, str]) -> list[list[str]]:
    """Names of keys that share a plaintext, one sorted group per value."""
    by_value: dict[str, list[str]] = {}
    for name, value in plaintexts.items():
        by_value.setdefault(value, []).append(name)
    return sorted(sorted(g) for g in by_value.values() if len(g) > 1)


def _shared_plaintext_names(plaintexts: dict[str, str]) -> set[str]:
    """The keys not to load: every key in a shared group, except the master
    key ("legacy"), which keeps its value."""
    return {
        name
        for group in _shared_plaintext_groups(plaintexts)
        for name in group
        if name != "legacy"
    }


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
