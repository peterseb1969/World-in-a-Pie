"""Field definition models for templates."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class FieldType(str, Enum):
    """Supported field types for template fields."""

    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TERM = "term"  # Reference to Def-Store terminology (legacy, use REFERENCE instead)
    REFERENCE = "reference"  # Unified reference to any WIP entity
    FILE = "file"  # Reference to a file entity (FILE-XXXXXX)
    OBJECT = "object"  # Nested template
    ARRAY = "array"  # Collection of items


class ReferenceType(str, Enum):
    """Types of entities that can be referenced."""

    DOCUMENT = "document"  # Reference to another document
    TERM = "term"  # Reference to a term in a terminology
    TERMINOLOGY = "terminology"  # Reference to a terminology itself
    TEMPLATE = "template"  # Reference to a template itself


class VersionStrategy(str, Enum):
    """How references are resolved over time."""

    LATEST = "latest"  # Always resolve to current active version
    PINNED = "pinned"  # Lock to specific version at creation time


class SemanticType(str, Enum):
    """
    Universal semantic types that provide meaning beyond base types.

    Semantic types add validation and transformation logic for commonly
    needed data patterns. They work with base types:
    - string: email, url
    - number: latitude, longitude, percentage
    - object: duration, geo_point
    """

    EMAIL = "email"  # RFC 5322 email address
    URL = "url"  # Valid HTTP(S) URL
    LATITUDE = "latitude"  # Geographic latitude (-90 to 90)
    LONGITUDE = "longitude"  # Geographic longitude (-180 to 180)
    PERCENTAGE = "percentage"  # Percentage value (0 to 100)
    DURATION = "duration"  # Time duration with unit {value, unit}
    GEO_POINT = "geo_point"  # Geographic point {latitude, longitude}


class FieldValidation(BaseModel):
    """Field-level validation constraints."""

    pattern: str | None = Field(
        default=None,
        description="Regex pattern for string fields"
    )
    min_length: int | None = Field(
        default=None,
        description="Minimum string length"
    )
    max_length: int | None = Field(
        default=None,
        description="Maximum string length"
    )
    minimum: float | None = Field(
        default=None,
        description="Minimum numeric value"
    )
    maximum: float | None = Field(
        default=None,
        description="Maximum numeric value"
    )
    enum: list[Any] | None = Field(
        default=None,
        description="Allowed values (not term-based)"
    )


class FileFieldConfig(BaseModel):
    """Configuration for file reference fields."""

    allowed_types: list[str] = Field(
        default=["*/*"],
        description="Allowed MIME type patterns (e.g., 'image/*', 'application/pdf')"
    )
    max_size_mb: float = Field(
        default=10.0,
        gt=0,
        le=100,
        description="Maximum file size in MB (max 100MB)"
    )
    multiple: bool = Field(
        default=False,
        description="Allow multiple files (field value becomes array of file IDs)"
    )
    max_files: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Maximum number of files when multiple=true (default: unlimited)"
    )


class FieldDefinition(BaseModel):
    """A field definition within a template."""

    name: str = Field(
        ...,
        description="Field name (used in data)",
        examples=["first_name", "birth_date"]
    )
    label: str = Field(
        ...,
        description="Human-readable label",
        examples=["First Name", "Date of Birth"]
    )
    type: FieldType = Field(
        ...,
        description="Data type"
    )
    mandatory: bool = Field(
        default=False,
        description="Whether field is required"
    )
    default_value: Any | None = Field(
        default=None,
        description="Default value if not provided"
    )

    # For type=term: reference to Def-Store terminology (legacy)
    terminology_ref: str | None = Field(
        default=None,
        description="Canonical terminology_id for term validation (resolved from value at creation)"
    )

    # For type=object: reference to another template
    template_ref: str | None = Field(
        default=None,
        description="Canonical template_id for nested template (resolved from value at creation)"
    )
    # The pinned version of the nested template. Mandatory whenever template_ref
    # is set (enforced at template create/activate/update). Nested-object data is
    # validated against this exact (template_ref, template_ref_version) pair —
    # never "latest" — so a parent document never strands when the nested
    # template ships a new, incompatible version (CASE-493). Immutable per
    # template version; re-point by authoring a new version of this template.
    template_ref_version: int | None = Field(
        default=None,
        description="Pinned version of the nested template (mandatory when template_ref is set)"
    )

    # For type=reference: unified reference configuration
    reference_type: ReferenceType | None = Field(
        default=None,
        description="Type of entity being referenced (for reference type)"
    )
    target_templates: list[str] | None = Field(
        default=None,
        description="Canonical template_ids for allowed document reference targets (resolved from values at creation)"
    )
    include_subtypes: bool | None = Field(
        default=None,
        description="When true, target_templates also accepts documents from child templates (via inheritance)"
    )
    target_terminologies: list[str] | None = Field(
        default=None,
        description="Canonical terminology_ids for allowed term reference targets (resolved from values at creation)"
    )
    version_strategy: VersionStrategy | None = Field(
        default=None,
        description="How to resolve reference versions (default: latest)"
    )

    # For type=file: file configuration
    file_config: FileFieldConfig | None = Field(
        default=None,
        description="Configuration for file fields (allowed types, size limits)"
    )

    # For type=array: item configuration
    array_item_type: FieldType | None = Field(
        default=None,
        description="Type of array items (for array type)"
    )
    array_terminology_ref: str | None = Field(
        default=None,
        description="Canonical terminology_id for array item term validation (resolved from value at creation)"
    )
    array_template_ref: str | None = Field(
        default=None,
        description="Canonical template_id for array item template (resolved from value at creation)"
    )
    # Pinned version of the array-item template. Mandatory whenever
    # array_template_ref is set (same rationale as template_ref_version — CASE-493).
    array_template_ref_version: int | None = Field(
        default=None,
        description="Pinned version of the array-item template (mandatory when array_template_ref is set)"
    )
    array_file_config: FileFieldConfig | None = Field(
        default=None,
        description="File configuration for array items if file type"
    )

    # Validation constraints
    validation: FieldValidation | None = Field(
        default=None,
        description="Field-level validation rules"
    )

    # Semantic type for universal data patterns
    semantic_type: SemanticType | None = Field(
        default=None,
        description="Semantic type for additional validation (email, url, latitude, etc.)"
    )

    # Postgres full-text indexing (reporting layer materialises a tsvector
    # column + GIN index on this field; the /api/reporting-sync/search
    # endpoint uses it for ranked, snippet-rich search). Only valid on
    # type=string fields, and requires the template's reporting.sync_enabled
    # to be true (validated at template creation).
    #
    # v1 accepts bool only. The schema reserves space for future per-field
    # language hints (e.g. "en", "de") — passing a string today raises a
    # validation error so the future expansion stays non-breaking.
    full_text_indexed: bool | None = Field(
        default=None,
        description=(
            "Enable PostgreSQL full-text indexing on this field. Only valid "
            "for type=string and requires template reporting.sync_enabled=true. "
            "Future: language codes ('en', 'de') will be accepted; v1 is bool only."
        )
    )

    # Inheritance tracking (populated during resolution, not stored)
    inherited: bool | None = Field(
        default=None,
        description="Whether this field is inherited from a parent template (set during resolution)"
    )
    inherited_from: str | None = Field(
        default=None,
        description="Template ID of the parent template this field was inherited from"
    )

    # Additional metadata
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional field metadata"
    )

    @model_validator(mode="after")
    def _require_pinned_nested_ref_versions(self) -> "FieldDefinition":
        """A nested template reference MUST pin an explicit version (CASE-493).

        Schema references never resolve to "latest" — a parent document
        validated against a floating nested schema can silently strand when
        that schema ships an incompatible new version. Enforced here so no
        write path (create, bulk, update, activation) can persist a nested
        ref without a pinned version. Existence of the pinned (template_id,
        version) pair is checked separately at the service layer (it needs a
        DB lookup); this guard only enforces presence.
        """
        if self.template_ref and self.template_ref_version is None:
            raise ValueError(
                f"template_ref_version is required for field '{self.name}': a "
                "nested template reference must pin an explicit version (CASE-493)"
            )
        if self.array_template_ref and self.array_template_ref_version is None:
            raise ValueError(
                f"array_template_ref_version is required for field '{self.name}': "
                "an array-item template reference must pin an explicit version "
                "(CASE-493)"
            )
        return self
