"""Namespace grant model for authorization.

A NamespaceGrant maps a subject (user email, API key name, or group)
to a permission level on a specific namespace.
"""

from datetime import UTC, datetime
from typing import Literal

from beanie import Document
from pydantic import BaseModel, Field
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

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(UTC) > self.expires_at

    class Settings:
        name = "namespace_grants"
        indexes = [
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

    subject: str
    subject_type: Literal["user", "api_key", "group"] = "user"
    permission: Literal["read", "write", "admin"] = "read"
    expires_at: datetime | None = None


class GrantRevoke(BaseModel):
    """Request to revoke a namespace grant."""

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


class GrantResponse(BaseModel):
    """A grant in API responses."""

    namespace: str
    subject: str
    subject_type: str
    permission: str
    granted_by: str
    granted_at: datetime
    expires_at: datetime | None = None


class MyNamespaceResponse(BaseModel):
    """A namespace the caller can access, with their permission level."""

    prefix: str
    description: str
    permission: str  # read, write, admin
