"""API request/response models for the Template Store service."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .field import FieldDefinition
from .rule import ValidationRule
from .template import TemplateMetadata, ReportingConfig


class StrictModel(BaseModel):
    """Base for API request models — rejects unknown fields."""
    model_config = ConfigDict(extra='forbid')


# =============================================================================
# TEMPLATE API MODELS
# =============================================================================

class CreateTemplateRequest(StrictModel):
    """Request to create a new template."""

    value: str = Field(
        ...,
        description="Human-readable value (e.g., 'PERSON')"
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
        description="Namespace for the template"
    )
    extends: Optional[str] = Field(
        None,
        description="Parent template ID for inheritance"
    )
    extends_version: Optional[int] = Field(
        None,
        description="Pinned parent version (None = always use latest active parent version)"
    )
    identity_fields: list[str] = Field(
        default_factory=list,
        description="Fields that form the composite identity key"
    )
    fields: list[FieldDefinition] = Field(
        default_factory=list,
        description="Field definitions"
    )
    rules: list[ValidationRule] = Field(
        default_factory=list,
        description="Cross-field validation rules"
    )
    metadata: Optional[TemplateMetadata] = Field(
        None,
        description="Additional metadata"
    )
    reporting: Optional[ReportingConfig] = Field(
        None,
        description="Configuration for PostgreSQL reporting sync"
    )
    created_by: Optional[str] = Field(
        None,
        description="User or system creating this template"
    )
    validate_references: bool = Field(
        default=True,
        description="Validate that terminology_ref and template_ref values exist before creating"
    )
    status: Optional[str] = Field(
        default=None,
        description="Initial status: 'active' (default) or 'draft' (skips reference validation)"
    )


class UpdateTemplateRequest(StrictModel):
    """Request to update an existing template."""

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
    extends: Optional[str] = Field(
        None,
        description="Parent template ID (changing creates new version)"
    )
    extends_version: Optional[int] = Field(
        None,
        description="Pinned parent version (None = always use latest active parent version)"
    )
    identity_fields: Optional[list[str]] = Field(
        None,
        description="Update identity fields"
    )
    fields: Optional[list[FieldDefinition]] = Field(
        None,
        description="Update field definitions"
    )
    rules: Optional[list[ValidationRule]] = Field(
        None,
        description="Update validation rules"
    )
    metadata: Optional[TemplateMetadata] = Field(
        None,
        description="Update metadata"
    )
    reporting: Optional[ReportingConfig] = Field(
        None,
        description="Update reporting configuration"
    )
    updated_by: Optional[str] = Field(
        None,
        description="User or system updating this template"
    )


class TemplateResponse(BaseModel):
    """Response containing template details."""

    template_id: str
    namespace: str
    value: str
    label: str
    description: Optional[str] = None
    version: int = 1
    extends: Optional[str] = None
    extends_version: Optional[int] = None
    identity_fields: list[str] = []
    fields: list[FieldDefinition] = []
    rules: list[ValidationRule] = []
    metadata: TemplateMetadata
    reporting: Optional[ReportingConfig] = None
    status: str
    created_at: datetime
    created_by: Optional[str] = None
    updated_at: datetime
    updated_by: Optional[str] = None


class TemplateListResponse(BaseModel):
    """Response for listing templates."""

    items: list[TemplateResponse]
    total: int
    page: int = 1
    page_size: int = 50


class TemplateUpdateResponse(BaseModel):
    """Response after updating a template."""

    template_id: str
    value: str
    version: int
    is_new_version: bool = Field(
        ...,
        description="True if a new version was created, False if unchanged"
    )
    previous_version: Optional[int] = Field(
        None,
        description="Previous version number if a new version was created"
    )


# =============================================================================
# BULK OPERATION MODELS
# =============================================================================

class BulkCreateTemplateRequest(StrictModel):
    """Request to create multiple templates at once."""

    templates: list[CreateTemplateRequest] = Field(
        ...,
        description="Templates to create"
    )
    created_by: Optional[str] = Field(
        None,
        description="User or system creating these templates"
    )


class BulkOperationResult(BaseModel):
    """Result of a bulk operation for a single item."""

    index: int
    status: str  # created, updated, error, skipped
    id: Optional[str] = None
    value: Optional[str] = None
    error: Optional[str] = None


class BulkOperationResponse(BaseModel):
    """Response for bulk operations."""

    results: list[BulkOperationResult]
    total: int
    succeeded: int
    failed: int


# =============================================================================
# VALIDATION MODELS
# =============================================================================

class ValidationError(BaseModel):
    """A validation error."""

    field: str
    code: str
    message: str


class ValidationWarning(BaseModel):
    """A validation warning."""

    field: str
    code: str
    message: str


class ValidateTemplateRequest(StrictModel):
    """Request to validate a template's references."""

    check_terminologies: bool = Field(
        default=True,
        description="Validate terminology references exist in Def-Store"
    )
    check_templates: bool = Field(
        default=True,
        description="Validate template references (extends, nested) exist"
    )


class ValidateTemplateResponse(BaseModel):
    """Response for template validation."""

    valid: bool
    template_id: str
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []
    will_also_activate: Optional[list[str]] = Field(
        default=None,
        description="Template IDs of other draft templates that would be activated together (draft templates only)"
    )


class ActivationDetail(BaseModel):
    """Detail for a single template in an activation operation."""

    template_id: str
    value: str
    status: str = Field(
        ...,
        description="Result: 'activated' or 'would_activate' (dry_run)"
    )


class ActivateTemplateResponse(BaseModel):
    """Response for template activation."""

    activated: list[str] = Field(
        default_factory=list,
        description="Template IDs that were activated"
    )
    activation_details: list[ActivationDetail] = Field(
        default_factory=list,
        description="Details for each template in the activation set"
    )
    total_activated: int = 0
    errors: list[ValidationError] = Field(
        default_factory=list,
        description="Validation errors preventing activation"
    )
    warnings: list[ValidationWarning] = Field(
        default_factory=list,
        description="Validation warnings"
    )


class CascadeResult(BaseModel):
    """Result of cascading a parent update to a single child template."""

    value: str
    old_template_id: str
    new_template_id: Optional[str] = None
    new_version: Optional[int] = None
    status: str = Field(
        ...,
        description="updated, unchanged, or error"
    )
    error: Optional[str] = None


class CascadeResponse(BaseModel):
    """Response for template cascade operation."""

    parent_template_id: str
    parent_value: str
    parent_version: int
    total: int
    updated: int
    unchanged: int
    failed: int
    results: list[CascadeResult]


class ValidateDocumentRequest(StrictModel):
    """Request to validate a document against a template."""

    data: dict[str, Any] = Field(
        ...,
        description="Document data to validate"
    )


class ValidateDocumentResponse(BaseModel):
    """Response for document validation."""

    valid: bool
    template_id: str
    identity_hash: Optional[str] = None
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []
