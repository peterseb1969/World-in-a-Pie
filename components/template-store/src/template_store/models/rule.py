"""Validation rule models for cross-field validation."""

from enum import Enum
from typing import Any, Literal, Optional

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
    value: Optional[Any] = Field(
        None,
        description="Value to compare (not needed for exists operators)"
    )


class ValidationRule(BaseModel):
    """A cross-field validation rule."""

    type: RuleType = Field(
        ...,
        description="Type of validation rule"
    )
    description: Optional[str] = Field(
        None,
        description="Human-readable description of the rule"
    )
    conditions: list[Condition] = Field(
        default_factory=list,
        description="Conditions that trigger the rule"
    )

    # For rules targeting a single field
    target_field: Optional[str] = Field(
        None,
        description="Field affected by the rule"
    )

    # For mutual_exclusion: fields that are mutually exclusive
    target_fields: Optional[list[str]] = Field(
        None,
        description="Fields affected (for mutual_exclusion)"
    )

    # For conditional_required
    required: Optional[bool] = Field(
        None,
        description="For conditional_required: is field required?"
    )

    # For conditional_value
    allowed_values: Optional[list[Any]] = Field(
        None,
        description="For conditional_value: allowed values"
    )

    # For pattern rule
    pattern: Optional[str] = Field(
        None,
        description="For pattern: regex pattern"
    )

    # For range rule
    minimum: Optional[float] = Field(
        None,
        description="For range: minimum value"
    )
    maximum: Optional[float] = Field(
        None,
        description="For range: maximum value"
    )

    # Error message
    error_message: Optional[str] = Field(
        None,
        description="Custom error message"
    )
