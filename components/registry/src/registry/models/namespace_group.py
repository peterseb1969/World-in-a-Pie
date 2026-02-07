"""Namespace Group model for managing related namespaces together."""

from datetime import datetime, timezone
from typing import Literal, Optional

from beanie import Document
from pydantic import Field, computed_field
from pymongo import IndexModel


class NamespaceGroup(Document):
    """
    A group of related namespaces managed together.

    A namespace group with prefix "dev" creates:
    - dev-terminologies
    - dev-terms
    - dev-templates
    - dev-documents
    - dev-files

    This enables:
    - Backup/restore of entire namespace groups
    - Dev/test environment isolation
    - Data migration between instances
    """

    prefix: str = Field(
        ...,
        description="Unique prefix for this group (e.g., 'wip', 'dev', 'customer-abc')"
    )
    description: str = Field(
        default="",
        description="Human-readable description of this namespace group"
    )
    isolation_mode: Literal["open", "strict"] = Field(
        default="open",
        description="'open' allows cross-namespace refs; 'strict' requires same-group only"
    )
    allowed_external_refs: list[str] = Field(
        default_factory=list,
        description="For open mode, optional allowlist of external namespace prefixes"
    )
    status: Literal["active", "archived", "deleted"] = Field(
        default="active",
        description="Group status"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_by: Optional[str] = Field(
        None,
        description="User who created this group"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_by: Optional[str] = Field(
        None,
        description="User who last updated this group"
    )

    # Computed properties for namespace names
    @computed_field
    @property
    def terminologies_ns(self) -> str:
        """Namespace for terminologies in this group."""
        return f"{self.prefix}-terminologies"

    @computed_field
    @property
    def terms_ns(self) -> str:
        """Namespace for terms in this group."""
        return f"{self.prefix}-terms"

    @computed_field
    @property
    def templates_ns(self) -> str:
        """Namespace for templates in this group."""
        return f"{self.prefix}-templates"

    @computed_field
    @property
    def documents_ns(self) -> str:
        """Namespace for documents in this group."""
        return f"{self.prefix}-documents"

    @computed_field
    @property
    def files_ns(self) -> str:
        """Namespace for files in this group."""
        return f"{self.prefix}-files"

    def get_all_namespaces(self) -> list[str]:
        """Get all namespace IDs in this group."""
        return [
            self.terminologies_ns,
            self.terms_ns,
            self.templates_ns,
            self.documents_ns,
            self.files_ns,
        ]

    class Settings:
        name = "namespace_groups"
        indexes = [
            IndexModel([("prefix", 1)], unique=True, name="prefix_unique_idx"),
            IndexModel([("status", 1)], name="status_idx"),
        ]
