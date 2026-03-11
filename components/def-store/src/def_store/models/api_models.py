"""API request/response models for the Def-Store service."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .terminology import TerminologyMetadata
from .term import TermTranslation


class StrictModel(BaseModel):
    """Base for API request models — rejects unknown fields."""
    model_config = ConfigDict(extra='forbid')


# =============================================================================
# TERMINOLOGY API MODELS
# =============================================================================

class CreateTerminologyRequest(StrictModel):
    """Request to create a new terminology."""

    value: str = Field(
        ...,
        description="Human-readable value (e.g., 'DOC_STATUS')"
    )
    label: str = Field(
        ...,
        description="Display label"
    )
    description: Optional[str] = Field(
        None,
        description="Detailed description"
    )
    namespace: str = Field(
        default="wip",
        description="Namespace for the terminology"
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


class UpdateTerminologyRequest(StrictModel):
    """Request to update an existing terminology."""

    value: Optional[str] = Field(
        None,
        description="New value (triggers Registry synonym)"
    )
    label: Optional[str] = Field(
        None,
        description="New display label"
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
    namespace: str
    value: str
    label: str
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
    pages: int = 0


# =============================================================================
# TERM API MODELS
# =============================================================================

class CreateTermRequest(StrictModel):
    """Request to create a new term."""

    value: str = Field(
        ...,
        description="The value stored in documents (unique within terminology)"
    )
    aliases: list[str] = Field(
        default_factory=list,
        description="Alternative values that resolve to this term (e.g., ['MR.', 'mr'])"
    )
    label: Optional[str] = Field(
        None,
        description="Display label for UI. Defaults to value if not provided."
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


class UpdateTermRequest(StrictModel):
    """Request to update an existing term."""

    value: Optional[str] = Field(
        None,
        description="New value (unique within terminology)"
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


class DeprecateTermRequest(StrictModel):
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
    namespace: str
    terminology_id: str
    terminology_value: Optional[str] = None
    value: str
    aliases: list[str] = []
    label: Optional[str] = None
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
    pages: int = 0
    terminology_id: str
    terminology_value: str


# =============================================================================
# BULK OPERATION MODELS
# =============================================================================

class BulkResultItem(BaseModel):
    """Result of a bulk operation for a single item."""

    index: int
    status: str  # created, updated, deleted, skipped, error
    id: Optional[str] = None
    value: Optional[str] = None
    error: Optional[str] = None


class BulkResponse(BaseModel):
    """Response for bulk operations."""

    results: list[BulkResultItem]
    total: int
    succeeded: int
    failed: int


class UpdateTerminologyItem(UpdateTerminologyRequest):
    """Item in a bulk terminology update request — includes the ID."""

    terminology_id: str = Field(..., description="ID of terminology to update")


class DeleteItem(StrictModel):
    """Item in a bulk delete request."""

    id: str = Field(..., description="ID of entity to delete")
    force: bool = Field(default=False, description="Force deletion even if dependencies exist")
    updated_by: Optional[str] = Field(None, description="User performing deletion")


class UpdateTermItem(UpdateTermRequest):
    """Item in a bulk term update request — includes the ID."""

    term_id: str = Field(..., description="ID of term to update")


class DeprecateTermItem(DeprecateTermRequest):
    """Item in a bulk term deprecate request — includes the ID."""

    term_id: str = Field(..., description="ID of term to deprecate")


# =============================================================================
# IMPORT/EXPORT MODELS
# =============================================================================

class ImportTerminologyRequest(StrictModel):
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

class ValidateValueRequest(StrictModel):
    """Request to validate a value against a terminology."""

    terminology_id: Optional[str] = Field(
        None,
        description="Terminology ID (use this or terminology_value)"
    )
    terminology_value: Optional[str] = Field(
        None,
        description="Terminology value (use this or id)"
    )
    value: str = Field(
        ...,
        description="Value to validate"
    )


class ValidateValueResponse(BaseModel):
    """Response for value validation."""

    valid: bool
    terminology_id: str
    terminology_value: str
    value: str
    matched_term: Optional[TermResponse] = None
    matched_via: Optional[str] = Field(
        None,
        description="How the match was made: 'value' or 'alias'"
    )
    suggestion: Optional[TermResponse] = Field(
        None,
        description="Suggested term if value is close but not exact"
    )
    error: Optional[str] = None


class BulkValidateRequest(StrictModel):
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


# =============================================================================
# ONTOLOGY / RELATIONSHIP MODELS
# =============================================================================

class CreateRelationshipRequest(StrictModel):
    """Request to create a typed relationship between two terms."""

    source_term_id: str = Field(
        ...,
        description="The subject term ID"
    )
    target_term_id: str = Field(
        ...,
        description="The object term ID"
    )
    relationship_type: str = Field(
        ...,
        description="Relationship type value (e.g., 'is_a', 'part_of')"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Provenance, confidence, OWL axioms"
    )
    created_by: Optional[str] = Field(
        None,
        description="User or system creating this relationship"
    )


class DeleteRelationshipRequest(StrictModel):
    """Request to delete a specific relationship."""

    source_term_id: str = Field(..., description="The subject term ID")
    target_term_id: str = Field(..., description="The object term ID")
    relationship_type: str = Field(..., description="Relationship type value")


class RelationshipResponse(BaseModel):
    """Response containing a single relationship."""

    namespace: str
    source_term_id: str
    target_term_id: str
    relationship_type: str
    relationship_value: Optional[str] = None
    source_terminology_id: Optional[str] = None
    target_terminology_id: Optional[str] = None
    metadata: dict[str, Any] = {}
    status: str
    created_at: datetime
    created_by: Optional[str] = None


class RelationshipListResponse(BaseModel):
    """Response for listing relationships."""

    items: list[RelationshipResponse]
    total: int
    page: int = 1
    page_size: int = 50
    pages: int = 0


class TraversalNode(BaseModel):
    """A single node in a traversal result."""

    term_id: str
    value: Optional[str] = None
    terminology_id: Optional[str] = None
    depth: int
    path: list[str]


class TraversalResponse(BaseModel):
    """Response for ancestor/descendant traversal queries."""

    term_id: str
    relationship_type: str
    direction: str  # "ancestors" or "descendants"
    nodes: list[TraversalNode]
    total: int
    max_depth_reached: bool = False
