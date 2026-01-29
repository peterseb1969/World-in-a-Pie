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
    TERM = "term"  # Reference to Def-Store terminology
    OBJECT = "object"  # Nested template
    ARRAY = "array"  # Collection of items


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

    # For type=term: reference to Def-Store terminology
    terminology_ref: Optional[str] = Field(
        None,
        description="Reference to terminology ID or code (for term type)"
    )

    # For type=object: reference to another template
    template_ref: Optional[str] = Field(
        None,
        description="Reference to nested template ID (for object type)"
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
