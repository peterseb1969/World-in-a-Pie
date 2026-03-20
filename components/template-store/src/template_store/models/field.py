"""Field definition models for templates."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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
        None,
        description="Regex pattern for string fields"
    )
    min_length: int | None = Field(
        None,
        description="Minimum string length"
    )
    max_length: int | None = Field(
        None,
        description="Maximum string length"
    )
    minimum: float | None = Field(
        None,
        description="Minimum numeric value"
    )
    maximum: float | None = Field(
        None,
        description="Maximum numeric value"
    )
    enum: list[Any] | None = Field(
        None,
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
        None,
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
        None,
        description="Default value if not provided"
    )

    # For type=term: reference to Def-Store terminology (legacy)
    terminology_ref: str | None = Field(
        None,
        description="Canonical terminology_id for term validation (resolved from value at creation)"
    )

    # For type=object: reference to another template
    template_ref: str | None = Field(
        None,
        description="Canonical template_id for nested template (resolved from value at creation)"
    )

    # For type=reference: unified reference configuration
    reference_type: ReferenceType | None = Field(
        None,
        description="Type of entity being referenced (for reference type)"
    )
    target_templates: list[str] | None = Field(
        None,
        description="Canonical template_ids for allowed document reference targets (resolved from values at creation)"
    )
    include_subtypes: bool | None = Field(
        None,
        description="When true, target_templates also accepts documents from child templates (via inheritance)"
    )
    target_terminologies: list[str] | None = Field(
        None,
        description="Canonical terminology_ids for allowed term reference targets (resolved from values at creation)"
    )
    version_strategy: VersionStrategy | None = Field(
        None,
        description="How to resolve reference versions (default: latest)"
    )

    # For type=file: file configuration
    file_config: FileFieldConfig | None = Field(
        None,
        description="Configuration for file fields (allowed types, size limits)"
    )

    # For type=array: item configuration
    array_item_type: FieldType | None = Field(
        None,
        description="Type of array items (for array type)"
    )
    array_terminology_ref: str | None = Field(
        None,
        description="Canonical terminology_id for array item term validation (resolved from value at creation)"
    )
    array_template_ref: str | None = Field(
        None,
        description="Canonical template_id for array item template (resolved from value at creation)"
    )
    array_file_config: FileFieldConfig | None = Field(
        None,
        description="File configuration for array items if file type"
    )

    # Validation constraints
    validation: FieldValidation | None = Field(
        None,
        description="Field-level validation rules"
    )

    # Semantic type for universal data patterns
    semantic_type: SemanticType | None = Field(
        None,
        description="Semantic type for additional validation (email, url, latitude, etc.)"
    )

    # Inheritance tracking (populated during resolution, not stored)
    inherited: bool | None = Field(
        None,
        description="Whether this field is inherited from a parent template (set during resolution)"
    )
    inherited_from: str | None = Field(
        None,
        description="Template ID of the parent template this field was inherited from"
    )

    # Additional metadata
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional field metadata"
    )
