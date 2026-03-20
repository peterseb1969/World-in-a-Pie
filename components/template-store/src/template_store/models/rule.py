"""Validation rule models for cross-field validation."""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RuleType(str, Enum):
    """Types of cross-field validation rules."""

    CONDITIONAL_REQUIRED = "conditional_required"  # Field required if condition met
    CONDITIONAL_VALUE = "conditional_value"  # Field value constrained by condition
    MUTUAL_EXCLUSION = "mutual_exclusion"  # Only one of listed fields can have value
    DEPENDENCY = "dependency"  # Field requires another field
    PATTERN = "pattern"  # Regex validation
    RANGE = "range"  # Numeric range validation


class Condition(BaseModel):
    """A condition for conditional rules."""

    field: str = Field(
        ...,
        description="Field to check"
    )
    operator: Literal["equals", "not_equals", "in", "not_in", "exists", "not_exists"] = Field(
        ...,
        description="Comparison operator"
    )
    value: Any | None = Field(
        None,
        description="Value to compare (not needed for exists operators)"
    )


class ValidationRule(BaseModel):
    """A cross-field validation rule."""

    type: RuleType = Field(
        ...,
        description="Type of validation rule"
    )
    description: str | None = Field(
        None,
        description="Human-readable description of the rule"
    )
    conditions: list[Condition] = Field(
        default_factory=list,
        description="Conditions that trigger the rule"
    )

    # For rules targeting a single field
    target_field: str | None = Field(
        None,
        description="Field affected by the rule"
    )

    # For mutual_exclusion: fields that are mutually exclusive
    target_fields: list[str] | None = Field(
        None,
        description="Fields affected (for mutual_exclusion)"
    )

    # For conditional_required
    required: bool | None = Field(
        None,
        description="For conditional_required: is field required?"
    )

    # For conditional_value
    allowed_values: list[Any] | None = Field(
        None,
        description="For conditional_value: allowed values"
    )

    # For pattern rule
    pattern: str | None = Field(
        None,
        description="For pattern: regex pattern"
    )

    # For range rule
    minimum: float | None = Field(
        None,
        description="For range: minimum value"
    )
    maximum: float | None = Field(
        None,
        description="For range: maximum value"
    )

    # Error message
    error_message: str | None = Field(
        None,
        description="Custom error message"
    )
