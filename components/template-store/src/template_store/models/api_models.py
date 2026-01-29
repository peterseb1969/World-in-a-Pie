"""API request/response models for the Template Store service."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from .field import FieldDefinition
from .rule import ValidationRule
from .template import TemplateMetadata


# =============================================================================
# TEMPLATE API MODELS
# =============================================================================

class CreateTemplateRequest(BaseModel):
    """Request to create a new template."""

    code: str = Field(
        ...,
        description="Human-readable code (e.g., 'PERSON')"
    )
    name: str = Field(
        ...,
        description="Display name"
    )
    description: Optional[str] = Field(
        None,
        description="Detailed description"
    )
    extends: Optional[str] = Field(
        None,
        description="Parent template ID for inheritance"
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
    created_by: Optional[str] = Field(
        None,
        description="User or system creating this template"
    )


class UpdateTemplateRequest(BaseModel):
    """Request to update an existing template."""

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
    extends: Optional[str] = Field(
        None,
        description="Parent template ID (changing creates new version)"
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
    updated_by: Optional[str] = Field(
        None,
        description="User or system updating this template"
    )


class TemplateResponse(BaseModel):
    """Response containing template details."""

    template_id: str
    code: str
    name: str
    description: Optional[str] = None
    version: int = 1
    extends: Optional[str] = None
    identity_fields: list[str] = []
    fields: list[FieldDefinition] = []
    rules: list[ValidationRule] = []
    metadata: TemplateMetadata
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


# =============================================================================
# BULK OPERATION MODELS
# =============================================================================

class BulkCreateTemplateRequest(BaseModel):
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
    code: Optional[str] = None
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


class ValidateTemplateRequest(BaseModel):
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


class ValidateDocumentRequest(BaseModel):
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
