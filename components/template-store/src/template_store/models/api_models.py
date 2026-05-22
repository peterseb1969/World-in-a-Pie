"""API request/response models for the Template Store service."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Canonical bulk-response models live in wip_auth.bulk_models (CASE-395).
# Re-exported here under the template-store-facing names so existing
# callers keep working without re-defining the schema.
from wip_auth.bulk_models import (
    TemplateBulkResponse as BulkResponse,  # noqa: F401
)
from wip_auth.bulk_models import (
    TemplateBulkResultItem as BulkResultItem,  # noqa: F401
)

from .field import FieldDefinition
from .rule import ValidationRule
from .template import ReportingConfig, TemplateMetadata, TemplateUsage


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
    description: str | None = Field(
        default=None,
        description="Detailed description"
    )
    template_id: str | None = Field(
        default=None,
        description="Pre-assigned template ID (for restore/migration — Registry uses as-is instead of generating)"
    )
    version: int | None = Field(
        default=None,
        description="Pre-assigned version (for restore/migration — skips Registry and version computation when used with template_id)"
    )
    namespace: str = Field(
        ...,
        description="Namespace for the template"
    )
    extends: str | None = Field(
        default=None,
        description="Parent template ID for inheritance"
    )
    extends_version: int | None = Field(
        default=None,
        description="Pinned parent version (None = always use latest active parent version)"
    )
    identity_fields: list[str] = Field(
        default_factory=list,
        description="Fields that form the composite identity key"
    )
    header_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Fields to include in peer/header projections (CASE-343). "
            "Bare names → data.<name>; metadata.custom.<name> paths allowed. "
            "Empty → projection falls back to identity_fields."
        )
    )
    usage: TemplateUsage = Field(
        default=TemplateUsage.ENTITY,
        description="Usage class: entity (default), reference, or relationship. Immutable after creation."
    )
    source_templates: list[str] = Field(
        default_factory=list,
        description="Template values allowed as edge source (required when usage=relationship; ignored otherwise)"
    )
    target_templates: list[str] = Field(
        default_factory=list,
        description="Template values allowed as edge target (required when usage=relationship; ignored otherwise)"
    )
    versioned: bool = Field(
        default=True,
        description="True = updates create new versions; False = overwrite in place. Immutable after creation."
    )
    fields: list[FieldDefinition] = Field(
        default_factory=list,
        description="Field definitions"
    )
    rules: list[ValidationRule] = Field(
        default_factory=list,
        description="Cross-field validation rules"
    )
    metadata: TemplateMetadata | None = Field(
        default=None,
        description="Additional metadata"
    )
    reporting: ReportingConfig | None = Field(
        default=None,
        description="Configuration for PostgreSQL reporting sync"
    )
    created_by: str | None = Field(
        default=None,
        description="User or system creating this template"
    )
    validate_references: bool = Field(
        default=True,
        description="Validate that terminology_ref and template_ref values exist before creating"
    )
    status: str | None = Field(
        default=None,
        description="Initial status: 'active' (default) or 'draft' (skips reference validation)"
    )


class UpdateTemplateRequest(StrictModel):
    """Request to update an existing template."""

    value: str | None = Field(
        default=None,
        description="New value (triggers Registry synonym)"
    )
    label: str | None = Field(
        default=None,
        description="New display label"
    )
    description: str | None = Field(
        default=None,
        description="New description"
    )
    extends: str | None = Field(
        default=None,
        description="Parent template ID (changing creates new version)"
    )
    extends_version: int | None = Field(
        default=None,
        description="Pinned parent version (None = always use latest active parent version)"
    )
    identity_fields: list[str] | None = Field(
        default=None,
        description="Update identity fields"
    )
    header_fields: list[str] | None = Field(
        default=None,
        description="Update peer-projection fields (CASE-343)"
    )
    fields: list[FieldDefinition] | None = Field(
        default=None,
        description="Update field definitions"
    )
    rules: list[ValidationRule] | None = Field(
        default=None,
        description="Update validation rules"
    )
    metadata: TemplateMetadata | None = Field(
        default=None,
        description="Update metadata"
    )
    reporting: ReportingConfig | None = Field(
        default=None,
        description="Update reporting configuration"
    )
    updated_by: str | None = Field(
        default=None,
        description="User or system updating this template"
    )


class TemplateResponse(BaseModel):
    """Response containing template details."""

    template_id: str
    namespace: str
    value: str
    label: str
    description: str | None = None
    version: int = 1
    extends: str | None = None
    extends_version: int | None = None
    identity_fields: list[str] = []
    header_fields: list[str] = []
    usage: TemplateUsage = TemplateUsage.ENTITY
    source_templates: list[str] = []
    target_templates: list[str] = []
    versioned: bool = True
    fields: list[FieldDefinition] = []
    rules: list[ValidationRule] = []
    metadata: TemplateMetadata
    reporting: ReportingConfig | None = None
    status: str
    created_at: datetime
    created_by: str | None = None
    updated_at: datetime
    updated_by: str | None = None


class TemplateListResponse(BaseModel):
    """Response for listing templates."""

    items: list[TemplateResponse]
    total: int
    page: int = 1
    page_size: int = 50
    pages: int = 0


class TemplateUpdateResponse(BaseModel):
    """Response after updating a template."""

    template_id: str
    value: str
    version: int
    is_new_version: bool = Field(
        ...,
        description="True if a new version was created, False if unchanged"
    )
    previous_version: int | None = Field(
        default=None,
        description="Previous version number if a new version was created"
    )


# =============================================================================
# BULK OPERATION MODELS
# =============================================================================
# Canonical models live in wip_auth.bulk_models (CASE-395) — imported at
# the top of this file and re-exported as BulkResponse / BulkResultItem.


class UpdateTemplateItem(UpdateTemplateRequest):
    """Item in a bulk template update request — includes the ID."""

    template_id: str = Field(..., description="ID of template to update")


class DeleteItem(StrictModel):
    """Item in a bulk delete request."""

    id: str = Field(..., description="ID of entity to delete")
    version: int | None = Field(default=None, description="Specific version to delete (default: latest for soft-delete, all for hard-delete)")
    force: bool = Field(default=False, description="Force deletion even if documents exist")
    hard_delete: bool = Field(default=False, description="Permanently remove (requires namespace deletion_mode='full')")
    updated_by: str | None = Field(default=None, description="User performing deletion")


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
    will_also_activate: list[str] | None = Field(
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
    new_template_id: str | None = None
    new_version: int | None = None
    status: str = Field(
        ...,
        description="updated, unchanged, or error"
    )
    error: str | None = None


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
    identity_hash: str | None = None
    errors: list[ValidationError] = []
    warnings: list[ValidationWarning] = []
