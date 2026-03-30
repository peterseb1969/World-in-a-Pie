"""API request and response models for the Document Store."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .document import DocumentMetadata, DocumentStatus
from .file import FileMetadata, FileStatus


class StrictModel(BaseModel):
    """Base for API request models — rejects unknown fields."""
    model_config = ConfigDict(extra='forbid')


# ============================================================================
# Document Creation
# ============================================================================

class DocumentCreateRequest(StrictModel):
    """Request to create or update a document."""

    template_id: str = Field(
        ...,
        description="Template ID to validate against"
    )
    template_version: int | None = Field(
        None,
        description="Specific template version to validate against (default: latest)"
    )
    document_id: str | None = Field(
        None,
        description="Pre-assigned document ID (for restore/migration — Registry uses as-is instead of generating)"
    )
    version: int | None = Field(
        None,
        description="Pre-assigned version (for restore/migration — skips Registry and version computation when used with document_id)"
    )
    namespace: str = Field(
        ...,
        description="Namespace for the document"
    )
    data: dict[str, Any] = Field(
        ...,
        description="Document content"
    )
    created_by: str | None = Field(
        None,
        description="User or system creating this document"
    )
    metadata: dict[str, Any] | None = Field(
        None,
        description="Custom metadata"
    )
    synonyms: list[dict[str, Any]] | None = Field(
        None,
        description="Optional synonym composite keys to register for this document in the Registry"
    )


class DocumentResponse(BaseModel):
    """Response containing a document."""

    document_id: str
    namespace: str
    template_id: str
    template_version: int
    template_value: str | None = Field(
        None,
        description="Template value (e.g., PLANNED_VISIT)"
    )
    identity_hash: str = ""
    version: int
    data: dict[str, Any]
    term_references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved term IDs for term fields (legacy)"
    )
    references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved references for reference type fields"
    )
    file_references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved file references for file fields"
    )
    status: DocumentStatus
    created_at: datetime
    created_by: str | None
    updated_at: datetime
    updated_by: str | None
    metadata: DocumentMetadata

    # Latest version info - populated when returning document
    is_latest_version: bool = Field(
        default=True,
        description="Whether this is the latest version of the document"
    )
    latest_version: int | None = Field(
        None,
        description="The latest version number for this document_id"
    )

    model_config = ConfigDict(from_attributes=True)


class DocumentCreateResponse(BaseModel):
    """Response after creating/updating a document."""

    document_id: str
    namespace: str
    template_id: str
    template_value: str | None = Field(
        None,
        description="Template value (e.g., PLANNED_VISIT)"
    )
    identity_hash: str = ""
    version: int
    is_new: bool = Field(
        ...,
        description="True if this is a new document, False if it's a new version"
    )
    previous_version: int | None = Field(
        None,
        description="Previous version number if this is an update"
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-blocking validation warnings"
    )


# ============================================================================
# Document Listing
# ============================================================================

class DocumentListResponse(BaseModel):
    """Response containing a list of documents."""

    items: list[DocumentResponse]
    total: int
    page: int
    page_size: int
    pages: int
    next_cursor: str | None = Field(
        None,
        description="Cursor for next page (MongoDB _id of last item). "
                    "Null on last page or when using offset pagination."
    )


# ============================================================================
# Document Versions
# ============================================================================

class DocumentVersionSummary(BaseModel):
    """Summary of a document version."""

    document_id: str
    version: int
    status: DocumentStatus
    created_at: datetime
    created_by: str | None


class DocumentVersionResponse(BaseModel):
    """Response containing document version history."""

    identity_hash: str = ""
    current_version: int
    versions: list[DocumentVersionSummary]


# ============================================================================
# Document Query
# ============================================================================

class QueryFilter(StrictModel):
    """A filter condition for document queries."""

    field: str = Field(
        ...,
        description="Field path to filter on (e.g., 'data.status', 'template_id')"
    )
    operator: str = Field(
        default="eq",
        description="Comparison operator: eq, ne, gt, gte, lt, lte, in, nin, exists, regex"
    )
    value: Any = Field(
        ...,
        description="Value to compare against"
    )


class DocumentQueryRequest(StrictModel):
    """Request for complex document queries."""

    filters: list[QueryFilter] = Field(
        default_factory=list,
        description="Filter conditions (AND logic)"
    )
    template_id: str | None = Field(
        None,
        description="Filter by template ID"
    )
    status: DocumentStatus | None = Field(
        DocumentStatus.ACTIVE,
        description="Filter by status (defaults to 'active' — pass null to include all versions)"
    )
    page: int = Field(
        default=1,
        ge=1,
        description="Page number"
    )
    page_size: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Items per page"
    )
    sort_by: str = Field(
        default="created_at",
        description="Field to sort by"
    )
    sort_order: str = Field(
        default="desc",
        description="Sort order: asc or desc"
    )


class DocumentQueryResponse(BaseModel):
    """Response for document queries."""

    items: list[DocumentResponse]
    total: int
    page: int
    page_size: int
    pages: int
    query: DocumentQueryRequest


# ============================================================================
# Bulk Operations
# ============================================================================

class BulkResultItem(BaseModel):
    """Result of a bulk operation for a single item."""

    index: int
    status: str  # created, updated, unchanged, deleted, skipped, error
    id: str | None = None
    document_id: str | None = None
    identity_hash: str | None = None
    version: int | None = None
    is_new: bool | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


class BulkResponse(BaseModel):
    """Response for bulk operations."""

    results: list[BulkResultItem]
    total: int
    succeeded: int
    failed: int
    timing: dict[str, float] | None = Field(
        default=None,
        description="Server-side timing breakdown in milliseconds"
    )


class DeleteItem(StrictModel):
    """Item in a bulk delete request."""

    id: str = Field(..., description="ID of entity to delete")
    version: int | None = Field(None, description="Specific version to hard-delete (default: all versions). Ignored for soft-delete.")
    force: bool = Field(default=False, description="Force deletion even if referenced")
    hard_delete: bool = Field(default=False, description="Permanently remove (requires namespace deletion_mode='full')")
    updated_by: str | None = Field(None, description="User performing deletion")


class ArchiveItem(StrictModel):
    """Item in a bulk archive request."""

    id: str = Field(..., description="ID of document to archive")
    archived_by: str | None = Field(None, description="User performing the archive")


# ============================================================================
# Validation
# ============================================================================

class ValidationError(BaseModel):
    """A validation error."""

    field: str | None = Field(
        None,
        description="Field path with the error (e.g., 'data.name')"
    )
    code: str = Field(
        ...,
        description="Error code (e.g., 'required', 'invalid_type', 'invalid_term')"
    )
    message: str = Field(
        ...,
        description="Human-readable error message"
    )
    details: dict[str, Any] | None = Field(
        None,
        description="Additional error details"
    )


class ValidationRequest(StrictModel):
    """Request to validate document data without saving."""

    template_id: str = Field(
        ...,
        description="Template ID to validate against"
    )
    namespace: str = Field(
        ...,
        description="Namespace for the document"
    )
    data: dict[str, Any] = Field(
        ...,
        description="Document data to validate"
    )


class ValidationResponse(BaseModel):
    """Response from document validation."""

    valid: bool = Field(
        ...,
        description="Whether the document is valid"
    )
    errors: list[ValidationError] = Field(
        default_factory=list,
        description="Validation errors"
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-blocking warnings"
    )
    identity_hash: str | None = Field(
        None,
        description="Computed identity hash (if valid)"
    )
    template_version: int | None = Field(
        None,
        description="Template version used for validation"
    )
    term_references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved term IDs for term fields (legacy)"
    )
    references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved references for reference type fields"
    )
    file_references: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Resolved file references for file fields"
    )


# ============================================================================
# File Management
# ============================================================================

class FileUploadMetadata(StrictModel):
    """Metadata to include with file upload."""

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
    allowed_templates: list[str] | None = Field(
        None,
        description="Template values that can reference this file (None = all)"
    )


class UpdateFileMetadataRequest(StrictModel):
    """Request to update file metadata."""

    description: str | None = Field(
        None,
        description="Human-readable description of the file"
    )
    tags: list[str] | None = Field(
        None,
        description="Searchable tags (replaces existing)"
    )
    category: str | None = Field(
        None,
        description="Classification category"
    )
    custom: dict[str, Any] | None = Field(
        None,
        description="Additional custom metadata fields (merges with existing)"
    )
    allowed_templates: list[str] | None = Field(
        None,
        description="Template values that can reference this file"
    )


class UpdateFileItem(UpdateFileMetadataRequest):
    """Item in a bulk file metadata update request — includes the ID."""

    file_id: str = Field(..., description="ID of file to update")


class FileResponse(BaseModel):
    """Response containing a file entity."""

    file_id: str
    filename: str
    content_type: str
    size_bytes: int
    checksum: str
    storage_key: str
    metadata: FileMetadata
    status: FileStatus
    reference_count: int
    allowed_templates: list[str] | None
    uploaded_at: datetime
    uploaded_by: str | None
    updated_at: datetime | None
    updated_by: str | None

    model_config = ConfigDict(from_attributes=True)


class FileListResponse(BaseModel):
    """Response containing a list of files."""

    items: list[FileResponse]
    total: int
    page: int
    page_size: int
    pages: int


class FileDownloadResponse(BaseModel):
    """Response containing a pre-signed download URL."""

    file_id: str
    filename: str
    content_type: str
    size_bytes: int
    download_url: str
    expires_in: int = Field(
        ...,
        description="URL expiration time in seconds"
    )


class FileIntegrityIssue(BaseModel):
    """A file integrity issue."""

    type: str = Field(
        ...,
        description="Issue type: orphan_file, missing_storage, broken_reference"
    )
    severity: str = Field(
        ...,
        description="Issue severity: warning, error"
    )
    file_id: str | None = None
    document_id: str | None = None
    field_path: str | None = None
    message: str


class FileIntegrityResponse(BaseModel):
    """Response from file integrity check."""

    status: str = Field(
        ...,
        description="healthy, warning, or error"
    )
    checked_at: datetime
    summary: dict[str, int] = Field(
        default_factory=dict,
        description="Count of issues by type"
    )
    issues: list[FileIntegrityIssue] = Field(
        default_factory=list,
        description="List of integrity issues found"
    )
