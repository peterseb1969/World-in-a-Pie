"""Data models for WIP authentication."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr


class UserIdentity(BaseModel):
    """Represents an authenticated user or service identity.

    This is the unified identity model returned by all auth providers.
    It contains the minimum information needed for authorization decisions
    and audit logging.
    """

    user_id: str = Field(..., description="Unique identifier for the user/service")
    username: str = Field(..., description="Human-readable name for display")
    email: str | None = Field(default=None, description="Email address (if available)")
    groups: list[str] = Field(default_factory=list, description="Group memberships for RBAC")
    auth_method: Literal["jwt", "api_key", "gateway_oidc", "none"] = Field(
        ..., description="How this identity was authenticated"
    )

    # Optional metadata
    provider: str | None = Field(default=None, description="Auth provider name (e.g., 'authelia', 'authentik')")
    raw_claims: dict | None = Field(default=None, description="Original JWT claims or API key metadata")

    @property
    def identity_string(self) -> str:
        """Generate string for created_by/updated_by fields.

        Format:
        - API key: "apikey:<username>"
        - JWT user: "<email>" (falls back to username, then user_id)
        - No auth: "anonymous"
        """
        if self.auth_method == "api_key":
            return f"apikey:{self.username}"
        elif self.auth_method in ("jwt", "gateway_oidc"):
            return self.email or self.username or f"user:{self.user_id}"
        return "anonymous"

    def has_group(self, group: str) -> bool:
        """Check if identity has a specific group."""
        return group in self.groups

    def has_any_group(self, groups: list[str]) -> bool:
        """Check if identity has at least one of the specified groups."""
        return any(g in self.groups for g in groups)

    def has_all_groups(self, groups: list[str]) -> bool:
        """Check if identity has all of the specified groups."""
        return all(g in self.groups for g in groups)


class APIKeyRecord(BaseModel):
    """Configuration for an API key.

    API keys are used for service-to-service authentication and
    can optionally be associated with specific permissions/groups.
    """

    name: str = Field(..., description="Human-readable name for the key")
    key_hash: str = Field(
        ...,
        description=(
            "Bcrypt hash of the API key (legacy SHA-256 hashes are accepted "
            "for verification only, with a deprecation warning)"
        ),
    )
    owner: str = Field(default="system", description="Owner of this key (user or service)")
    groups: list[str] = Field(default_factory=list, description="Groups/roles for this key")
    description: str | None = Field(default=None, description="Description of what this key is for")
    created_at: datetime | None = Field(default=None, description="When the key was created")
    last_used_at: datetime | None = Field(default=None, description="When the key was last used")
    expires_at: datetime | None = Field(default=None, description="When the key expires (None = never)")
    enabled: bool = Field(default=True, description="Whether the key is active")

    # Optional: namespace-scoped permissions (for Registry compatibility)
    namespaces: list[str] | None = Field(
        default=None,
        description="If set, limits key to these namespaces only"
    )

    # Config-declared grants: {namespace: "read"|"write"|"admin"}, as the
    # deployer's key file declares them. The Registry imports them as the
    # key's grants and resolves them; a service never answers a permission
    # from them itself.
    grants: dict[str, Literal["read", "write", "admin"]] | None = Field(
        default=None,
        description=(
            "Config-declared namespace grants, resolved by the Registry"
        ),
    )

    # SHA-256 of the salted plaintext (what bcrypt hashes), kept for config
    # keys loaded from a plaintext so the Registry can tell whether a runtime
    # key's hash matches a config key's value. Not a field: it never
    # serializes. None for runtime keys and pre-hashed config entries.
    _plaintext_prehash: bytes | None = PrivateAttr(default=None)
    # The plaintext itself, for the same config keys: the Registry compares a
    # config key with runtime hashes made under the runtime-key salt, which
    # can differ from the salt this process hashes its config keys with. The
    # process already holds the plaintext (env, key file); it never
    # serializes and never leaves the process.
    _plaintext: str | None = PrivateAttr(default=None)

    @property
    def plaintext_prehash(self) -> bytes | None:
        return self._plaintext_prehash

    def plaintext_prehash_under(self, salt: str) -> bytes | None:
        """The prehash of this config key's plaintext under `salt`, or None
        when the key was given pre-hashed."""
        if self._plaintext is None:
            return None
        from .providers.api_key import api_key_prehash

        return api_key_prehash(self._plaintext, salt)

    def is_expired(self) -> bool:
        """Check if the key has expired.

        Compares against a UTC-aware now, matching the rest of the system
        (registry models and the provider's last_used_at all use
        datetime.now(UTC)). A naive expires_at is assumed to be UTC rather
        than compared against a naive local now — otherwise the answer would
        be off by the host's UTC offset.
        """
        if self.expires_at is None:
            return False
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return datetime.now(UTC) > expires_at


class AuthResult(BaseModel):
    """Result of an authentication attempt."""

    success: bool = Field(..., description="Whether authentication succeeded")
    identity: UserIdentity | None = Field(default=None, description="Identity if authenticated")
    error: str | None = Field(default=None, description="Error message if failed")
    error_code: str | None = Field(default=None, description="Machine-readable error code")
