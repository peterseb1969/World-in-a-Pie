"""API request and response models for the Document Store."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .document import DocumentStatus, DocumentMetadata


# ============================================================================
# Document Creation
# ============================================================================

class DocumentCreateRequest(BaseModel):
    """Request to create or update a document."""

    template_id: str = Field(
        ...,
        description="Template ID to validate against"
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


class DocumentResponse(BaseModel):
    """Response containing a document."""

    document_id: str
    template_id: str
    template_version: int
    identity_hash: str
    version: int
    data: dict[str, Any]
    status: DocumentStatus
    created_at: datetime
    created_by: Optional[str]
    updated_at: datetime
    updated_by: Optional[str]
    metadata: DocumentMetadata

    model_config = ConfigDict(from_attributes=True)


class DocumentCreateResponse(BaseModel):
    """Response after creating/updating a document."""

    document_id: str
    template_id: str
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

class QueryFilter(BaseModel):
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


class DocumentQueryRequest(BaseModel):
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
        description="created, updated, or error"
    )
    document_id: Optional[str] = None
    identity_hash: Optional[str] = None
    version: Optional[int] = None
    is_new: Optional[bool] = None
    error: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class BulkCreateRequest(BaseModel):
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
    failed: int
    results: list[BulkCreateResult]


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


class ValidationRequest(BaseModel):
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
