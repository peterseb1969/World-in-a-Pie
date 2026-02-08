"""Namespace model for user-facing namespace management.

A Namespace (e.g., "wip", "dev", "prod") is a user-facing container for
organizing data. Each Namespace automatically creates 5 ID Pools for
ID generation:
- {prefix}-terminologies
- {prefix}-terms
- {prefix}-templates
- {prefix}-documents
- {prefix}-files
"""

from datetime import datetime, timezone
from typing import Literal, Optional

from beanie import Document
from pydantic import Field, computed_field
from pymongo import IndexModel


class Namespace(Document):
    """
    A user-facing namespace for organizing data.

    Users work with Namespaces (e.g., "wip", "dev", "prod"). Each Namespace
    automatically creates 5 ID Pools for ID generation per entity type.

    This enables:
    - Backup/restore of entire namespaces
    - Dev/test environment isolation
    - Data migration between instances
    """

    prefix: str = Field(
        ...,
        description="Unique prefix for this namespace (e.g., 'wip', 'dev', 'customer-abc')"
    )
    description: str = Field(
        default="",
        description="Human-readable description of this namespace"
    )
    isolation_mode: Literal["open", "strict"] = Field(
        default="open",
        description="'open' allows cross-namespace refs; 'strict' requires same-namespace only"
    )
    allowed_external_refs: list[str] = Field(
        default_factory=list,
        description="For open mode, optional allowlist of external namespace prefixes"
    )
    status: Literal["active", "archived", "deleted"] = Field(
        default="active",
        description="Namespace status"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_by: Optional[str] = Field(
        None,
        description="User who created this namespace"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_by: Optional[str] = Field(
        None,
        description="User who last updated this namespace"
    )

    # Computed properties for ID pool names
    @computed_field
    @property
    def terminologies_pool(self) -> str:
        """ID pool for terminologies in this namespace."""
        return f"{self.prefix}-terminologies"

    @computed_field
    @property
    def terms_pool(self) -> str:
        """ID pool for terms in this namespace."""
        return f"{self.prefix}-terms"

    @computed_field
    @property
    def templates_pool(self) -> str:
        """ID pool for templates in this namespace."""
        return f"{self.prefix}-templates"

    @computed_field
    @property
    def documents_pool(self) -> str:
        """ID pool for documents in this namespace."""
        return f"{self.prefix}-documents"

    @computed_field
    @property
    def files_pool(self) -> str:
        """ID pool for files in this namespace."""
        return f"{self.prefix}-files"

    def get_all_pools(self) -> list[str]:
        """Get all ID pool names in this namespace."""
        return [
            self.terminologies_pool,
            self.terms_pool,
            self.templates_pool,
            self.documents_pool,
            self.files_pool,
        ]

    class Settings:
        name = "namespaces"
        indexes = [
            IndexModel([("prefix", 1)], unique=True, name="prefix_unique_idx"),
            IndexModel([("status", 1)], name="status_idx"),
        ]
