"""Namespace grant model for authorization.

A NamespaceGrant maps a subject (user email, API key name, or group)
to a permission level on a specific namespace.
"""

from datetime import UTC, datetime
from typing import ClassVar, Literal

from beanie import Document
from pydantic import BaseModel, ConfigDict, Field
from pymongo import IndexModel

from wip_auth.bulk_models import BulkResponseBase, BulkResultItemBase


class NamespaceGrant(Document):
    """A permission grant for a subject on a namespace."""

    namespace: str = Field(
        ..., description="Namespace prefix this grant applies to"
    )
    subject: str = Field(
        ..., description="Who gets access: email (OIDC), API key name, or group name"
    )
    subject_type: Literal["user", "api_key", "group"] = Field(
        ..., description="Type of subject"
    )
    permission: Literal["read", "write", "admin"] = Field(
        ..., description="Permission level: read < write < admin"
    )
    granted_by: str = Field(
        ..., description="Identity string of who created this grant"
    )
    granted_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )
    expires_at: datetime | None = Field(
        default=None, description="Optional expiration (None = never)"
    )
    managed_by: Literal["wip-deploy"] | None = Field(
        default=None,
        description="'wip-deploy': imported from a key's grants in the install's key file",
    )
    origin: Literal["file", "console", "restored"] = Field(
        default="console",
        description="Who wrote the grant: the install's key file, the API, or a restore",
    )
    orphaned: bool = Field(
        default=False,
        description=(
            "Written by a system restore for a namespace that is not here: no "
            "namespace or reservation on this install has the identity "
            "(namespace_id) the grant was made for. An orphaned grant counts as "
            "no grant until it is activated: when a namespace with its "
            "namespace_id is created, by whoever creates it, or when a namespace "
            "admin activates it. A key's namespaces list does not stand in for "
            "it: the list is a scope and gives no access by itself."
        ),
    )
    orphaned_at: datetime | None = Field(
        default=None, description="When the grant was written orphaned"
    )
    namespace_id: str | None = Field(
        default=None,
        description=(
            "On an orphaned grant: the identity of the namespace it was made "
            "for, which a namespace created with that id activates. Unset on "
            "an effective grant, which belongs to whichever namespace holds "
            "its name."
        ),
    )

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        # MongoDB hands datetimes back without a timezone; a stored expiry is
        # UTC, so a naive one is read as such (comparing it with an aware
        # now raises TypeError).
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return datetime.now(UTC) > expires_at

    class Settings:
        name = "namespace_grants"
        indexes: ClassVar = [
            IndexModel(
                [("namespace", 1), ("subject", 1), ("subject_type", 1)],
                unique=True,
                name="grant_unique_idx",
            ),
            IndexModel([("subject", 1)], name="subject_idx"),
            IndexModel([("namespace", 1)], name="namespace_idx"),
        ]


# -- API models --

class GrantCreate(BaseModel):
    """Request to create a namespace grant."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    subject_type: Literal["user", "api_key", "group"] = "user"
    permission: Literal["read", "write", "admin"] = "read"
    expires_at: datetime | None = None


class GrantRevoke(BaseModel):
    """Request to revoke a namespace grant."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    subject_type: Literal["user", "api_key", "group"] = "user"


class GrantActivate(BaseModel):
    """Names an orphaned grant to activate."""

    model_config = ConfigDict(extra="forbid")

    subject: str
    subject_type: Literal["user", "api_key", "group"] = "user"


class GrantBulkResultItem(BulkResultItemBase):
    """Per-item result of a bulk grant create/upsert.

    Inherits the platform-universal envelope key `index` (plus status /
    error / error_code / details) from the canonical base — the same
    contract document-store, template-store, and def-store emit.
    Status values here: created, updated, error.
    """

    subject: str
    permission: str | None = None


class GrantBulkResponse(BulkResponseBase[GrantBulkResultItem]):
    """Envelope for the bulk grant-create response."""


class GrantRevokeBulkResultItem(BulkResultItemBase):
    """Per-item result of a bulk grant revoke. Status: revoked, not_found."""

    subject: str


class GrantRevokeBulkResponse(BulkResponseBase[GrantRevokeBulkResultItem]):
    """Envelope for the bulk grant-revoke response."""


class GrantActivateBulkResultItem(BulkResultItemBase):
    """Per-item result of a bulk orphan activation. Status: activated,
    unchanged (the grant is not orphaned), not_found."""

    subject: str


class GrantActivateBulkResponse(BulkResponseBase[GrantActivateBulkResultItem]):
    """Envelope for the bulk orphan-activation response."""


class GrantResponse(BaseModel):
    """A grant in API responses."""

    namespace: str
    subject: str
    subject_type: str
    permission: str
    granted_by: str
    granted_at: datetime
    expires_at: datetime | None = None
    managed_by: Literal["wip-deploy"] | None = Field(
        default=None,
        description="'wip-deploy' for a grant imported from the install's key file; "
        "the API refuses to change it (error_code managed_by_deployer)",
    )
    orphaned: bool = Field(
        default=False,
        description="True for a grant a system restore wrote before its namespace "
        "existed; it counts as no grant until activated",
    )
    orphaned_at: datetime | None = None


class GrantListResponse(BaseModel):
    """A page of grants across namespaces."""

    items: list[GrantResponse]
    total: int
    page: int
    page_size: int
    pages: int = 0


class MyNamespaceResponse(BaseModel):
    """A namespace the caller can access, with their permission level."""

    prefix: str
    description: str
    permission: str  # read, write, admin
