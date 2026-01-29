"""Document model for the Document Store service."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from beanie import Document as BeanieDocument
from pydantic import BaseModel, Field
from pymongo import IndexModel, DESCENDING


class DocumentStatus(str, Enum):
    """Status values for documents."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class DocumentMetadata(BaseModel):
    """Additional metadata for a document."""

    source_system: Optional[str] = Field(
        None,
        description="System that created this document"
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Validation warnings (non-blocking issues)"
    )
    custom: dict[str, Any] = Field(
        default_factory=dict,
        description="Custom metadata fields"
    )


class Document(BeanieDocument):
    """
    A document conforming to a template.

    Documents are validated against templates from the Template Store.
    They support versioning through identity-based upsert logic.

    The identity_hash is computed from the template's identity_fields,
    allowing the system to detect when a document should be updated
    (new version) rather than created as a new entity.
    """

    # Identity (from Registry - UUID7 for time-ordering)
    document_id: str = Field(
        ...,
        description="Unique ID from Registry (UUID7)"
    )

    # Template reference
    template_id: str = Field(
        ...,
        description="Reference to Template Store template ID"
    )
    template_version: int = Field(
        ...,
        description="Version of template used for validation"
    )

    # Identity for upsert logic
    identity_hash: str = Field(
        ...,
        description="SHA-256 hash of identity field values"
    )

    # Document versioning
    version: int = Field(
        default=1,
        description="Document version (per identity)"
    )

    # Document content
    data: dict[str, Any] = Field(
        ...,
        description="Document content conforming to template"
    )

    # Lifecycle
    status: DocumentStatus = Field(
        default=DocumentStatus.ACTIVE,
        description="Document status"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    created_by: Optional[str] = Field(
        None,
        description="User or system that created this document"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_by: Optional[str] = Field(
        None,
        description="User or system that last updated this document"
    )

    # Metadata
    metadata: DocumentMetadata = Field(
        default_factory=DocumentMetadata,
        description="Additional document metadata"
    )

    class Settings:
        name = "documents"
        indexes = [
            # Unique document ID
            IndexModel([("document_id", 1)], unique=True, name="document_id_unique_idx"),
            # Version lookup by identity
            IndexModel([("identity_hash", 1), ("version", 1)], name="identity_version_idx"),
            # Active document lookup by identity
            IndexModel([("identity_hash", 1), ("status", 1)], name="identity_status_idx"),
            # Template queries
            IndexModel([("template_id", 1), ("status", 1)], name="template_status_idx"),
            # Time-based queries (UUID7 provides ordering, but created_at is useful too)
            IndexModel([("created_at", DESCENDING)], name="created_at_idx"),
            # Composite for common queries
            IndexModel(
                [("template_id", 1), ("status", 1), ("created_at", DESCENDING)],
                name="template_status_time_idx"
            ),
        ]
