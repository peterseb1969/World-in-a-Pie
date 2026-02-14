"""API request and response models for the Document Store."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .document import DocumentStatus, DocumentMetadata
from .file import FileStatus, FileMetadata


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
    namespace: str = Field(
        default="wip",
        description="Namespace for the document"
    )
    data: dict[str, Any] = Field(
        ...,
        description="Document content"
    )
    created_by: Optional[str] = Field(
        None,
        description="User or system creating this document"
    )
    metadata: Optional[dict[str, Any]] = Field(
        None,
        description="Custom metadata"
    )
    synonyms: Optional[list[dict[str, Any]]] = Field(
        None,
        description="Optional synonym composite keys to register for this document in the Registry"
    )


class DocumentResponse(BaseModel):
    """Response containing a document."""

    document_id: str
    namespace: str
    template_id: str
    template_version: int
    template_value: Optional[str] = Field(
        None,
        description="Template value (e.g., PLANNED_VISIT)"
    )
    identity_hash: str
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
    created_by: Optional[str]
    updated_at: datetime
    updated_by: Optional[str]
    metadata: DocumentMetadata

    # Latest version info - populated when returning document
    is_latest_version: bool = Field(
        default=True,
        description="Whether this is the latest version of the document"
    )
    latest_version: Optional[int] = Field(
        None,
        description="The latest version number for this identity"
    )
    latest_document_id: Optional[str] = Field(
        None,
        description="Document ID of the latest version"
    )

    model_config = ConfigDict(from_attributes=True)


class DocumentCreateResponse(BaseModel):
    """Response after creating/updating a document."""

    document_id: str
    namespace: str
    template_id: str
    template_value: Optional[str] = Field(
        None,
        description="Template value (e.g., PLANNED_VISIT)"
    )
    identity_hash: str
    version: int
    is_new: bool = Field(
        ...,
        description="True if this is a new document, False if it's a new version"
    )
    previous_version: Optional[int] = Field(
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


# ============================================================================
# Document Versions
# ============================================================================

class DocumentVersionSummary(BaseModel):
    """Summary of a document version."""

    document_id: str
    version: int
    status: DocumentStatus
    created_at: datetime
    created_by: Optional[str]


class DocumentVersionResponse(BaseModel):
    """Response containing document version history."""

    identity_hash: str
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
    template_id: Optional[str] = Field(
        None,
        description="Filter by template ID"
    )
    status: Optional[DocumentStatus] = Field(
        None,
        description="Filter by status"
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

class BulkCreateResult(BaseModel):
    """Result for a single item in bulk create."""

    index: int
    status: str = Field(
        ...,
        description="created, updated, unchanged, skipped, or error"
    )
    document_id: Optional[str] = None
    identity_hash: Optional[str] = None
    version: Optional[int] = None
    is_new: Optional[bool] = None
    error: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class BulkCreateRequest(StrictModel):
    """Request for bulk document creation."""

    items: list[DocumentCreateRequest] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Documents to create (max 100)"
    )
    continue_on_error: bool = Field(
        default=True,
        description="Continue processing if an item fails"
    )


class BulkCreateResponse(BaseModel):
    """Response for bulk document creation."""

    total: int
    created: int
    updated: int
    unchanged: int = 0
    failed: int
    results: list[BulkCreateResult]
    timing: Optional[dict[str, float]] = Field(
        default=None,
        description="Server-side timing breakdown in milliseconds"
    )


# ============================================================================
# Validation
# ============================================================================

class ValidationError(BaseModel):
    """A validation error."""

    field: Optional[str] = Field(
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
    details: Optional[dict[str, Any]] = Field(
        None,
        description="Additional error details"
    )


class ValidationRequest(StrictModel):
    """Request to validate document data without saving."""

    template_id: str = Field(
        ...,
        description="Template ID to validate against"
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
    identity_hash: Optional[str] = Field(
        None,
        description="Computed identity hash (if valid)"
    )
    template_version: Optional[int] = Field(
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

    description: Optional[str] = Field(
        None,
        description="Human-readable description of the file"
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Searchable tags"
    )
    category: Optional[str] = Field(
        None,
        description="Classification category"
    )
    custom: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional custom metadata fields"
    )
    allowed_templates: Optional[list[str]] = Field(
        None,
        description="Template values that can reference this file (None = all)"
    )


class UpdateFileMetadataRequest(StrictModel):
    """Request to update file metadata."""

    description: Optional[str] = Field(
        None,
        description="Human-readable description of the file"
    )
    tags: Optional[list[str]] = Field(
        None,
        description="Searchable tags (replaces existing)"
    )
    category: Optional[str] = Field(
        None,
        description="Classification category"
    )
    custom: Optional[dict[str, Any]] = Field(
        None,
        description="Additional custom metadata fields (merges with existing)"
    )
    allowed_templates: Optional[list[str]] = Field(
        None,
        description="Template values that can reference this file"
    )


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
    allowed_templates: Optional[list[str]]
    uploaded_at: datetime
    uploaded_by: Optional[str]
    updated_at: Optional[datetime]
    updated_by: Optional[str]

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


class FileBulkResult(BaseModel):
    """Result for a single item in bulk file operations."""

    index: int
    status: str = Field(
        ...,
        description="success or error"
    )
    file_id: Optional[str] = None
    error: Optional[str] = None


class FileBulkDeleteRequest(StrictModel):
    """Request for bulk file deletion."""

    file_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="File IDs to delete (max 100)"
    )


class FileBulkDeleteResponse(BaseModel):
    """Response for bulk file deletion."""

    total: int
    deleted: int
    failed: int
    results: list[FileBulkResult]


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
    file_id: Optional[str] = None
    document_id: Optional[str] = None
    field_path: Optional[str] = None
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
