"""API request/response models for the Def-Store service."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from .terminology import TerminologyMetadata
from .term import TermTranslation


# =============================================================================
# TERMINOLOGY API MODELS
# =============================================================================

class CreateTerminologyRequest(BaseModel):
    """Request to create a new terminology."""

    code: str = Field(
        ...,
        description="Human-readable code (e.g., 'DOC_STATUS')"
    )
    name: str = Field(
        ...,
        description="Display name"
    )
    description: Optional[str] = Field(
        None,
        description="Detailed description"
    )
    case_sensitive: bool = Field(
        default=False,
        description="Whether term values are case-sensitive"
    )
    allow_multiple: bool = Field(
        default=False,
        description="Whether multiple terms can be selected"
    )
    extensible: bool = Field(
        default=False,
        description="Whether users can add new terms at runtime"
    )
    metadata: Optional[TerminologyMetadata] = Field(
        None,
        description="Additional metadata"
    )
    created_by: Optional[str] = Field(
        None,
        description="User or system creating this terminology"
    )


class UpdateTerminologyRequest(BaseModel):
    """Request to update an existing terminology."""

    code: Optional[str] = Field(
        None,
        description="New code (triggers Registry synonym)"
    )
    name: Optional[str] = Field(
        None,
        description="New display name"
    )
    description: Optional[str] = Field(
        None,
        description="New description"
    )
    case_sensitive: Optional[bool] = Field(
        None,
        description="Update case sensitivity"
    )
    allow_multiple: Optional[bool] = Field(
        None,
        description="Update multi-select setting"
    )
    extensible: Optional[bool] = Field(
        None,
        description="Update extensibility"
    )
    metadata: Optional[TerminologyMetadata] = Field(
        None,
        description="Update metadata"
    )
    updated_by: Optional[str] = Field(
        None,
        description="User or system updating this terminology"
    )


class TerminologyResponse(BaseModel):
    """Response containing terminology details."""

    terminology_id: str
    code: str
    name: str
    description: Optional[str] = None
    case_sensitive: bool = False
    allow_multiple: bool = False
    extensible: bool = False
    metadata: TerminologyMetadata
    status: str
    term_count: int = 0
    created_at: datetime
    created_by: Optional[str] = None
    updated_at: datetime
    updated_by: Optional[str] = None


class TerminologyListResponse(BaseModel):
    """Response for listing terminologies."""

    items: list[TerminologyResponse]
    total: int
    page: int = 1
    page_size: int = 50


# =============================================================================
# TERM API MODELS
# =============================================================================

class CreateTermRequest(BaseModel):
    """Request to create a new term."""

    code: str = Field(
        ...,
        description="Human-readable code (e.g., 'APPROVED')"
    )
    value: str = Field(
        ...,
        description="The value stored in documents"
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative values that resolve to this term (e.g., ['MR.', 'mr'])"
    )
    label: str = Field(
        ...,
        description="Display label for UI"
    )
    description: Optional[str] = Field(
        None,
        description="Detailed description"
    )
    sort_order: int = Field(
        default=0,
        description="Sort order within terminology"
    )
    parent_term_id: Optional[str] = Field(
        None,
        description="Parent term ID for hierarchical terms"
    )
    translations: list[TermTranslation] = Field(
        default_factory=list,
        description="Translations"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Custom metadata"
    )
    created_by: Optional[str] = Field(
        None,
        description="User or system creating this term"
    )


class UpdateTermRequest(BaseModel):
    """Request to update an existing term."""

    code: Optional[str] = Field(
        None,
        description="New code (triggers Registry synonym)"
    )
    value: Optional[str] = Field(
        None,
        description="New value"
    )
    aliases: Optional[list[str]] = Field(
        None,
        description="Update aliases (replaces existing list)"
    )
    label: Optional[str] = Field(
        None,
        description="New display label"
    )
    description: Optional[str] = Field(
        None,
        description="New description"
    )
    sort_order: Optional[int] = Field(
        None,
        description="New sort order"
    )
    parent_term_id: Optional[str] = Field(
        None,
        description="New parent term ID"
    )
    translations: Optional[list[TermTranslation]] = Field(
        None,
        description="Update translations"
    )
    metadata: Optional[dict[str, Any]] = Field(
        None,
        description="Update metadata (merged with existing)"
    )
    updated_by: Optional[str] = Field(
        None,
        description="User or system updating this term"
    )


class DeprecateTermRequest(BaseModel):
    """Request to deprecate a term."""

    reason: str = Field(
        ...,
        description="Why this term is being deprecated"
    )
    replaced_by_term_id: Optional[str] = Field(
        None,
        description="ID of the replacement term"
    )
    updated_by: Optional[str] = Field(
        None,
        description="User or system deprecating this term"
    )


class TermResponse(BaseModel):
    """Response containing term details."""

    term_id: str
    terminology_id: str
    terminology_code: Optional[str] = None
    code: str
    value: str
    aliases: list[str] = []
    label: str
    description: Optional[str] = None
    sort_order: int = 0
    parent_term_id: Optional[str] = None
    translations: list[TermTranslation] = []
    metadata: dict[str, Any] = {}
    status: str
    deprecated_reason: Optional[str] = None
    replaced_by_term_id: Optional[str] = None
    created_at: datetime
    created_by: Optional[str] = None
    updated_at: datetime
    updated_by: Optional[str] = None


class TermListResponse(BaseModel):
    """Response for listing terms."""

    items: list[TermResponse]
    total: int
    page: int = 1
    page_size: int = 50
    terminology_id: str
    terminology_code: str


# =============================================================================
# BULK OPERATION MODELS
# =============================================================================

class BulkCreateTermRequest(BaseModel):
    """Request to create multiple terms at once."""

    terms: list[CreateTermRequest] = Field(
        ...,
        description="Terms to create"
    )
    created_by: Optional[str] = Field(
        None,
        description="User or system creating these terms"
    )


class BulkOperationResult(BaseModel):
    """Result of a bulk operation for a single item."""

    index: int
    status: str  # created, updated, error, skipped
    id: Optional[str] = None
    code: Optional[str] = None
    error: Optional[str] = None


class BulkOperationResponse(BaseModel):
    """Response for bulk operations."""

    results: list[BulkOperationResult]
    total: int
    succeeded: int
    failed: int


# =============================================================================
# IMPORT/EXPORT MODELS
# =============================================================================

class ImportTerminologyRequest(BaseModel):
    """Request to import a terminology with terms."""

    terminology: CreateTerminologyRequest
    terms: list[CreateTermRequest] = Field(
        default_factory=list,
        description="Terms to import"
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Import options (e.g., skip_duplicates, update_existing)"
    )


class ExportFormat(BaseModel):
    """Supported export formats."""

    format: str = Field(
        default="json",
        description="Export format: json, csv, xml"
    )
    include_metadata: bool = Field(
        default=True,
        description="Include metadata in export"
    )
    include_inactive: bool = Field(
        default=False,
        description="Include inactive/deprecated terms"
    )
    languages: list[str] = Field(
        default_factory=list,
        description="Languages to include (empty = all)"
    )


class ExportTerminologyResponse(BaseModel):
    """Exported terminology with terms."""

    terminology: TerminologyResponse
    terms: list[TermResponse]
    export_date: datetime
    export_format: str


# =============================================================================
# VALIDATION MODELS
# =============================================================================

class ValidateValueRequest(BaseModel):
    """Request to validate a value against a terminology."""

    terminology_id: Optional[str] = Field(
        None,
        description="Terminology ID (use this or code)"
    )
    terminology_code: Optional[str] = Field(
        None,
        description="Terminology code (use this or id)"
    )
    value: str = Field(
        ...,
        description="Value to validate"
    )


class ValidateValueResponse(BaseModel):
    """Response for value validation."""

    valid: bool
    terminology_id: str
    terminology_code: str
    value: str
    matched_term: Optional[TermResponse] = None
    matched_via: Optional[str] = Field(
        None,
        description="How the match was made: 'code', 'value', or 'alias'"
    )
    suggestion: Optional[TermResponse] = Field(
        None,
        description="Suggested term if value is close but not exact"
    )
    error: Optional[str] = None


class BulkValidateRequest(BaseModel):
    """Request to validate multiple values."""

    items: list[ValidateValueRequest]


class BulkValidateResponse(BaseModel):
    """Response for bulk validation."""

    results: list[ValidateValueResponse]
    total: int
    valid_count: int
    invalid_count: int


# =============================================================================
# AUDIT LOG MODELS
# =============================================================================

class AuditLogEntry(BaseModel):
    """A single audit log entry."""

    term_id: str
    terminology_id: str
    action: str = Field(
        ...,
        description="Type of change: created, updated, deprecated, deleted"
    )
    changed_at: datetime
    changed_by: Optional[str] = None
    changed_fields: list[str] = []
    previous_values: dict[str, Any] = {}
    new_values: dict[str, Any] = {}
    comment: Optional[str] = None


class AuditLogResponse(BaseModel):
    """Response for audit log queries."""

    items: list[AuditLogEntry]
    total: int
    page: int = 1
    page_size: int = 50
