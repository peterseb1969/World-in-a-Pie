"""Beanie document model and request/response schemas for runtime API keys.

Runtime API keys are stored in MongoDB and managed via REST endpoints.
They coexist with config-file keys (loaded at startup via wip-auth).
"""

import secrets
from datetime import UTC, datetime

from beanie import Document
from pydantic import BaseModel, ConfigDict, Field
from pymongo import IndexModel


class StoredAPIKey(Document):
    """A runtime API key persisted in MongoDB.

    Config-file keys (wip-admins, wip-services) are NOT stored here —
    they live in the wip-auth config and are loaded at startup.
    """

    name: str = Field(..., description="Unique human-readable key name")
    key_hash: str = Field(..., description="Bcrypt hash of the plaintext key")
    owner: str = Field(default="system", description="Owner identifier")
    groups: list[str] = Field(default_factory=list, description="Authorization groups")
    description: str | None = Field(None, description="What this key is for")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = Field(None, description="Expiration (None = never)")
    enabled: bool = Field(default=True, description="Whether the key is active")
    namespaces: list[str] | None = Field(
        None, description="Namespace scope (None = unrestricted)"
    )
    created_by: str = Field(
        default="system", description="Identity string of the admin who created this key"
    )

    class Settings:
        name = "api_keys"
        indexes = [
            IndexModel([("name", 1)], unique=True, name="api_key_name_unique"),
        ]


def generate_plaintext_key() -> str:
    """Generate a cryptographically secure plaintext API key."""
    return secrets.token_urlsafe(32)


# --- Request / Response models ---


class APIKeyCreateRequest(BaseModel):
    """Request body for creating a new runtime API key."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Unique name for the key")
    owner: str = Field(default="system", description="Owner identifier")
    groups: list[str] = Field(default_factory=list, description="Authorization groups")
    description: str | None = Field(None, description="What this key is for")
    expires_at: datetime | None = Field(None, description="Expiration (None = never)")
    namespaces: list[str] | None = Field(
        None, description="Namespace scope (None = unrestricted)"
    )


class APIKeyResponse(BaseModel):
    """Response model for API key metadata (no hash, no plaintext)."""

    name: str
    owner: str
    groups: list[str]
    description: str | None
    created_at: datetime
    expires_at: datetime | None
    enabled: bool
    namespaces: list[str] | None
    created_by: str
    source: str = Field(description="'config' or 'runtime'")


class APIKeyCreatedResponse(APIKeyResponse):
    """Response after creating a key — includes plaintext shown once."""

    plaintext_key: str = Field(description="The plaintext key (shown once, not stored)")


class APIKeyUpdateRequest(BaseModel):
    """Request body for updating a runtime API key."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = None
    groups: list[str] | None = None
    namespaces: list[str] | None = None
    expires_at: datetime | None = None
    enabled: bool | None = None


class APIKeySyncRecord(BaseModel):
    """Record returned by the sync endpoint (includes hash for service polling)."""

    name: str
    key_hash: str
    owner: str
    groups: list[str]
    description: str | None
    created_at: datetime
    expires_at: datetime | None
    enabled: bool
    namespaces: list[str] | None
