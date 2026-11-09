"""Beanie document model and request/response schemas for runtime API keys.

Runtime API keys are stored in MongoDB and managed via REST endpoints.
They coexist with config-file keys (loaded at startup via wip-auth).

A key imported from the deployer's key file is stored here too, marked
`managed_by: "wip-deploy"` (services/key_import.py). The API refuses to
change a managed record; wip-deploy owns it.
"""

import secrets
from datetime import UTC, datetime
from typing import ClassVar, Literal

from beanie import Document
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import IndexModel

from ..services.reservations import ReservationSettings


class StoredAPIKey(Document):
    """An API key persisted in MongoDB: a Console key made through the API,
    a restored one, or a key imported from the deployer's key file
    (`managed_by: "wip-deploy"`, `origin: "file"`).

    Keys loaded only from the wip-auth config (the environment and the
    master key) are not stored here. An imported key-file key is a record
    here, and stays dormant in the provider while the Registry also loads
    the same file as config keys.
    """

    name: str = Field(..., description="Unique human-readable key name")
    key_hash: str = Field(..., description="Bcrypt hash of the plaintext key")
    owner: str = Field(default="system", description="Owner identifier")
    groups: list[str] = Field(default_factory=list, description="Authorization groups")
    description: str | None = Field(default=None, description="What this key is for")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = Field(default=None, description="Expiration (None = never)")
    enabled: bool = Field(default=True, description="Whether the key is active")
    namespaces: list[str] | None = Field(
        default=None, description="Namespace scope (None = unrestricted)"
    )
    created_by: str = Field(
        default="system", description="Identity string of the admin who created this key"
    )
    managed_by: Literal["wip-deploy"] | None = Field(
        default=None,
        description="'wip-deploy': imported from the install's key file; the API refuses to change it",
    )
    origin: Literal["file", "console", "restored"] = Field(
        default="console",
        description="Who wrote the record: the install's key file, the API (Console), or a restore",
    )
    grants_managed: bool = Field(
        default=False,
        description=(
            "A managed key whose file entry declares grants: its grants are "
            "imported and the API refuses to change them. A managed key that "
            "declares none keeps its grants under the API."
        ),
    )

    class Settings:
        name = "api_keys"
        indexes: ClassVar[list] = [
            IndexModel([("name", 1)], unique=True, name="api_key_name_unique"),
        ]


def generate_plaintext_key() -> str:
    """Generate a cryptographically secure plaintext API key."""
    return secrets.token_urlsafe(32)


# --- Request / Response models ---


class NamespaceReserveRequest(BaseModel):
    """A namespace a runtime key reserves, with the settings it is created with."""

    model_config = ConfigDict(extra="forbid")

    prefix: str = Field(..., description="The namespace to reserve")
    settings: ReservationSettings = Field(
        default_factory=ReservationSettings,
        description=(
            "Settings the namespace is created with; omitted ones take the "
            "platform defaults. The key's bootstrap may omit them or send equal "
            "values, never different ones."
        ),
    )


# A caller authenticated with a key presents its user_id as "apikey:<name>".
# For callers on an older wip-auth, grants, reservations and grant writes
# read that spelling as the bare name, so a key whose own name starts with it
# would act as the other key: its grants, its reservations. No key may take
# such a name.
RESERVED_KEY_NAME_PREFIX = "apikey:"


def key_name_error(name: str) -> str | None:
    """Why `name` cannot name an API key, or None when it can."""
    if name.startswith(RESERVED_KEY_NAME_PREFIX):
        return (
            f"key name {name!r} starts with {RESERVED_KEY_NAME_PREFIX!r}, the spelling "
            f"by which the key {name.removeprefix(RESERVED_KEY_NAME_PREFIX)!r} is "
            "addressed; choose another name"
        )
    return None


class APIKeyCreateRequest(BaseModel):
    """Request body for creating a new runtime API key."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ..., description="Unique name for the key; must not start with 'apikey:'",
    )

    @field_validator("name")
    @classmethod
    def _name_is_free(cls, v: str) -> str:
        error = key_name_error(v)
        if error:
            raise ValueError(error)
        return v
    owner: str = Field(default="system", description="Owner identifier")
    groups: list[str] = Field(default_factory=list, description="Authorization groups")
    description: str | None = Field(default=None, description="What this key is for")
    expires_at: datetime | None = Field(default=None, description="Expiration (None = never)")
    namespaces: list[str] | None = Field(
        default=None, description="Namespace scope (None = unrestricted)"
    )
    grant_permission: Literal["read", "write", "admin"] | None = Field(
        default=None,
        description=(
            "When set, create a namespace grant at this level for "
            "the new key (subject = key name) on each namespace in "
            "`namespaces`. Requires `namespaces` to be set. Without it the "
            "key gets a read grant on each: the namespaces list is a scope "
            "and gives no access by itself, so the key's access on a "
            "namespace is always a grant, which ends when the namespace is "
            "deleted or the grant is revoked."
        ),
    )
    reserve: list[NamespaceReserveRequest] | None = Field(
        default=None,
        description=(
            "Namespaces this key reserves, each listed in `namespaces`. The key "
            "gets `admin` on each, and may create it (PUT /namespaces/{prefix}) "
            "with the reserved settings: a key that bootstraps its app without "
            "being a platform admin. For apps wip-deploy does not manage; a "
            "prefix the install's reservations hold is refused. Revoking the key "
            "releases each reservation whose namespace was not created."
        ),
    )


class NamespaceAddRequest(BaseModel):
    """Widen a runtime key to one more namespace."""

    model_config = ConfigDict(extra="forbid")

    permission: Literal["read", "write", "admin"] | None = Field(
        default=None,
        description="The grant on the namespace; 'admin' when reserving, else 'read' by default",
    )
    reserve: ReservationSettings | None = Field(
        default=None,
        description=(
            "Reserve the namespace for the key with these settings (send {} for "
            "the defaults), so the key may create it. Requires permission admin "
            "(the default when reserving)."
        ),
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
    grants: dict[str, str] | None = Field(
        default=None,
        description=(
            "The grants a key file declares for this key ({namespace: "
            "read|write|admin}): on a config key, and on a key the Registry "
            "imported from the key file (managed_by wip-deploy) whose entry "
            "declares grants. Those grants are the file's; the API refuses "
            "to change them. None for every other key: its grants are "
            "Registry grants, changed through the API."
        ),
    )
    shadowed_by: str | None = Field(
        default=None,
        description=(
            "For a runtime key: the config key that has the same plaintext. "
            "A shadowed runtime key does not authenticate (the plaintext is "
            "the config key's) and is left out of key sync; revoke it, or "
            "give the config key a new value. Always None for config keys."
        ),
    )
    reserved_namespaces: list[str] | None = Field(
        default=None,
        description="For a runtime key: the namespaces it reserves. None for config keys.",
    )
    managed_by: Literal["wip-deploy"] | None = Field(
        default=None,
        description=(
            "'wip-deploy' when the Registry holds this key as a record imported "
            "from the install's key file. The API refuses to change it "
            "(409, error_code managed_by_deployer); change it with wip-deploy."
        ),
    )
    origin: Literal["file", "console", "restored"] | None = Field(
        default=None,
        description="For a stored record: the key file, the API (Console), or a restore",
    )


class APIKeyCreatedResponse(APIKeyResponse):
    """Response after creating a key — includes plaintext shown once."""

    plaintext_key: str = Field(description="The plaintext key (shown once, not stored)")
    granted_namespaces: list[str] | None = Field(
        default=None,
        description=(
            "Namespaces a grant was created on: every namespace in `namespaces` "
            "(at grant_permission, read without it, admin where reserved)"
        ),
    )


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


class APIKeyListResponse(BaseModel):
    """Paginated response for listing API keys (per wip://conventions)."""

    items: list[APIKeyResponse]
    total: int
    page: int = 1
    page_size: int = 50
    pages: int = 0
