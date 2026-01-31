"""Data models for WIP authentication."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class UserIdentity(BaseModel):
    """Represents an authenticated user or service identity.

    This is the unified identity model returned by all auth providers.
    It contains the minimum information needed for authorization decisions
    and audit logging.
    """

    user_id: str = Field(..., description="Unique identifier for the user/service")
    username: str = Field(..., description="Human-readable name for display")
    email: str | None = Field(None, description="Email address (if available)")
    groups: list[str] = Field(default_factory=list, description="Group memberships for RBAC")
    auth_method: Literal["jwt", "api_key", "none"] = Field(
        ..., description="How this identity was authenticated"
    )

    # Optional metadata
    provider: str | None = Field(None, description="Auth provider name (e.g., 'authelia', 'authentik')")
    raw_claims: dict | None = Field(None, description="Original JWT claims or API key metadata")

    @property
    def identity_string(self) -> str:
        """Generate string for created_by/updated_by fields.

        Format:
        - API key: "apikey:<username>"
        - JWT user: "user:<user_id>"
        - No auth: "anonymous"
        """
        if self.auth_method == "api_key":
            return f"apikey:{self.username}"
        elif self.auth_method == "jwt":
            return f"user:{self.user_id}"
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
    key_hash: str = Field(..., description="SHA-256 hash of the API key")
    owner: str = Field(default="system", description="Owner of this key (user or service)")
    groups: list[str] = Field(default_factory=list, description="Groups/roles for this key")
    description: str | None = Field(None, description="Description of what this key is for")
    created_at: datetime | None = Field(None, description="When the key was created")
    last_used_at: datetime | None = Field(None, description="When the key was last used")
    enabled: bool = Field(default=True, description="Whether the key is active")

    # Optional: namespace-scoped permissions (for Registry compatibility)
    namespaces: list[str] | None = Field(
        None,
        description="If set, limits key to these namespaces only"
    )


class AuthResult(BaseModel):
    """Result of an authentication attempt."""

    success: bool = Field(..., description="Whether authentication succeeded")
    identity: UserIdentity | None = Field(None, description="Identity if authenticated")
    error: str | None = Field(None, description="Error message if failed")
    error_code: str | None = Field(None, description="Machine-readable error code")
