"""Field definition models for templates."""

from enum import Enum
from typing import Any, Optional

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


class FieldValidation(BaseModel):
    """Field-level validation constraints."""

    pattern: Optional[str] = Field(
        None,
        description="Regex pattern for string fields"
    )
    min_length: Optional[int] = Field(
        None,
        description="Minimum string length"
    )
    max_length: Optional[int] = Field(
        None,
        description="Maximum string length"
    )
    minimum: Optional[float] = Field(
        None,
        description="Minimum numeric value"
    )
    maximum: Optional[float] = Field(
        None,
        description="Maximum numeric value"
    )
    enum: Optional[list[Any]] = Field(
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
    max_files: Optional[int] = Field(
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
    default_value: Optional[Any] = Field(
        None,
        description="Default value if not provided"
    )

    # For type=term: reference to Def-Store terminology (legacy)
    terminology_ref: Optional[str] = Field(
        None,
        description="Reference to terminology ID or code (for term type)"
    )

    # For type=object: reference to another template
    template_ref: Optional[str] = Field(
        None,
        description="Reference to nested template ID (for object type)"
    )

    # For type=reference: unified reference configuration
    reference_type: Optional[ReferenceType] = Field(
        None,
        description="Type of entity being referenced (for reference type)"
    )
    target_templates: Optional[list[str]] = Field(
        None,
        description="Allowed template codes for document references"
    )
    target_terminologies: Optional[list[str]] = Field(
        None,
        description="Allowed terminology codes for term references"
    )
    version_strategy: Optional[VersionStrategy] = Field(
        None,
        description="How to resolve reference versions (default: latest)"
    )

    # For type=file: file configuration
    file_config: Optional[FileFieldConfig] = Field(
        None,
        description="Configuration for file fields (allowed types, size limits)"
    )

    # For type=array: item configuration
    array_item_type: Optional[FieldType] = Field(
        None,
        description="Type of array items (for array type)"
    )
    array_terminology_ref: Optional[str] = Field(
        None,
        description="Terminology reference for array items if term type"
    )
    array_template_ref: Optional[str] = Field(
        None,
        description="Template reference for array items if object type"
    )
    array_file_config: Optional[FileFieldConfig] = Field(
        None,
        description="File configuration for array items if file type"
    )

    # Validation constraints
    validation: Optional[FieldValidation] = Field(
        None,
        description="Field-level validation rules"
    )

    # Additional metadata
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional field metadata"
    )
