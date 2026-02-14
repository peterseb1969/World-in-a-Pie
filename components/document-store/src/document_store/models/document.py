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

    # Namespace for multi-tenant isolation
    namespace: str = Field(
        default="wip",
        description="Namespace for data isolation (e.g., wip, dev, seed)"
    )

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
    template_value: Optional[str] = Field(
        None,
        description="Template value (e.g., PLANNED_VISIT) for easier identification"
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

    # Term references - resolved term IDs for term fields (legacy, use references instead)
    # Array format for indexing: [{"field_path": "gender", "term_id": "T-001"}, ...]
    term_references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved term IDs for term fields"
    )

    # Unified references - resolved references for all reference type fields
    # Array format: [{"field_path": "supervisor", "reference_type": "document", "resolved": {...}}, ...]
    references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved references for reference type fields"
    )

    # File references - resolved file IDs for file fields
    # Array format: [{"field_path": "scan_image", "file_id": "FILE-000001", "filename": "...", ...}, ...]
    file_references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved file references for file fields"
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
            # Unique document ID within namespace
            IndexModel([("namespace", 1), ("document_id", 1)], unique=True, name="ns_document_id_unique_idx"),
            # Version lookup by identity within namespace
            IndexModel([("namespace", 1), ("identity_hash", 1), ("version", 1)], name="ns_identity_version_idx"),
            # Active document lookup by identity within namespace
            IndexModel([("namespace", 1), ("identity_hash", 1), ("status", 1)], name="ns_identity_status_idx"),
            # Template queries within namespace
            IndexModel([("namespace", 1), ("template_id", 1), ("status", 1)], name="ns_template_status_idx"),
            # Time-based queries within namespace
            IndexModel([("namespace", 1), ("created_at", DESCENDING)], name="ns_created_at_idx"),
            # Composite for common queries within namespace
            IndexModel(
                [("namespace", 1), ("template_id", 1), ("status", 1), ("created_at", DESCENDING)],
                name="ns_template_status_time_idx"
            ),
            # Global document_id lookup (for cross-namespace refs in open mode)
            IndexModel([("document_id", 1)], unique=True, name="document_id_unique_idx"),
            # Term reference reverse lookups (find documents referencing a term)
            IndexModel(
                [("term_references.term_id", 1)],
                name="term_references_term_id_idx",
                sparse=True
            ),
            # Reference reverse lookups (find documents referencing another document)
            IndexModel(
                [("references.resolved.document_id", 1)],
                name="references_document_id_idx",
                sparse=True
            ),
            IndexModel(
                [("references.resolved.identity_hash", 1)],
                name="references_identity_hash_idx",
                sparse=True
            ),
            IndexModel(
                [("references.resolved.term_id", 1)],
                name="references_term_id_idx",
                sparse=True
            ),
            IndexModel(
                [("references.resolved.template_id", 1)],
                name="references_template_id_idx",
                sparse=True
            ),
            IndexModel(
                [("references.resolved.terminology_id", 1)],
                name="references_terminology_id_idx",
                sparse=True
            ),
            # File reference reverse lookups (find documents referencing a file)
            IndexModel(
                [("file_references.file_id", 1)],
                name="file_references_file_id_idx",
                sparse=True
            ),
        ]
