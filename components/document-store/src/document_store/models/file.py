"""File model for the Document Store service.

Files are first-class entities with Registry IDs, stored in MinIO,
and referenced by documents like any other reference type.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from beanie import Document as BeanieDocument
from pydantic import BaseModel, Field
from pymongo import DESCENDING, IndexModel


class FileStatus(str, Enum):
    """Status values for files."""
    ORPHAN = "orphan"      # Uploaded but not referenced by any active document
    ACTIVE = "active"      # Referenced by at least one active document
    INACTIVE = "inactive"  # Soft-deleted


class FileMetadata(BaseModel):
    """User-defined metadata for files."""

    description: str | None = Field(
        None,
        description="Human-readable description of the file"
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Searchable tags"
    )
    category: str | None = Field(
        None,
        description="Classification category"
    )
    custom: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional custom metadata fields"
    )


class File(BeanieDocument):
    """
    A file entity stored in MinIO.

    Files are first-class entities with Registry IDs.
    They can be shared across multiple documents and support
    orphan detection for cleanup.

    The storage_key in MinIO is simply the file_id for easy mapping.
    """

    # Namespace for multi-tenant isolation
    namespace: str = Field(
        ...,
        description="Namespace for data isolation (e.g., wip, dev, seed)"
    )

    # Identity (from Registry)
    file_id: str = Field(
        ...,
        description="Unique ID from Registry"
    )

    # Core file properties
    filename: str = Field(
        ...,
        description="Original filename"
    )
    content_type: str = Field(
        ...,
        description="MIME type (e.g., 'image/jpeg', 'application/pdf')"
    )
    size_bytes: int = Field(
        ...,
        ge=0,
        description="File size in bytes"
    )
    checksum: str = Field(
        ...,
        description="SHA-256 checksum of file content"
    )
    storage_key: str = Field(
        ...,
        description="Key in MinIO/S3 (same as file_id)"
    )

    # User-defined metadata
    metadata: FileMetadata = Field(
        default_factory=FileMetadata,
        description="User-defined metadata"
    )

    # Status tracking
    status: FileStatus = Field(
        default=FileStatus.ORPHAN,
        description="File status (orphan until referenced by a document)"
    )
    reference_count: int = Field(
        default=0,
        ge=0,
        description="Number of active documents referencing this file"
    )

    # Optional: restrict which templates can use this file
    allowed_templates: list[str] | None = Field(
        None,
        description="Template values that can reference this file (None = all)"
    )

    # Lifecycle
    uploaded_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the file was uploaded"
    )
    uploaded_by: str | None = Field(
        None,
        description="User or system that uploaded this file"
    )
    updated_at: datetime | None = Field(
        None,
        description="When the file metadata was last updated"
    )
    updated_by: str | None = Field(
        None,
        description="User or system that last updated this file"
    )

    class Settings:
        name = "files"
        indexes = [
            # Unique file ID within namespace
            IndexModel([("namespace", 1), ("file_id", 1)], unique=True, name="ns_file_id_unique_idx"),
            # Status queries within namespace
            IndexModel([("namespace", 1), ("status", 1)], name="ns_file_status_idx"),
            # Duplicate detection by checksum within namespace
            IndexModel([("namespace", 1), ("checksum", 1)], name="ns_file_checksum_idx"),
            # Content type filter within namespace
            IndexModel([("namespace", 1), ("content_type", 1)], name="ns_file_content_type_idx"),
            # Orphan detection within namespace
            IndexModel(
                [("namespace", 1), ("status", 1), ("reference_count", 1)],
                name="ns_file_orphan_idx"
            ),
            # Time-based queries within namespace
            IndexModel([("namespace", 1), ("uploaded_at", DESCENDING)], name="ns_file_uploaded_at_idx"),
            # Tag search (global - typically tags are namespace-agnostic)
            IndexModel([("metadata.tags", 1)], name="file_tags_idx", sparse=True),
            # Category filter
            IndexModel([("metadata.category", 1)], name="file_category_idx", sparse=True),
            # Uploader queries
            IndexModel([("uploaded_by", 1)], name="file_uploaded_by_idx", sparse=True),
        ]


class FileReference(BaseModel):
    """
    Reference to a file entity, stored in document.file_references.

    Similar to term_references, this stores both the file_id and
    denormalized metadata for display purposes.
    """

    field_path: str = Field(
        ...,
        description="Path to the field in document data"
    )
    file_id: str = Field(
        ...,
        description="Reference to file entity"
    )
    # Denormalized for display (avoids extra lookups)
    filename: str = Field(
        ...,
        description="Original filename (denormalized)"
    )
    content_type: str = Field(
        ...,
        description="MIME type (denormalized)"
    )
    size_bytes: int = Field(
        ...,
        description="File size in bytes (denormalized)"
    )
    description: str | None = Field(
        None,
        description="File description (denormalized from file metadata)"
    )
