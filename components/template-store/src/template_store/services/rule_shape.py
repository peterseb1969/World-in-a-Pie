"""What each validation rule type needs before document-store can evaluate it.

A rule is a promise about every document of its template. One that names no
field, a condition 'in' a single value, or a pattern that does not compile
would either crash document validation or be skipped there, and a constraint
the platform skips is a guardrail that holds only sometimes. Each check
returns why a rule cannot be evaluated, or None when it can.
"""

import math
import re
from collections.abc import Callable

from ..models.rule import RuleType, ValidationRule

_NEEDS_TARGET = {
    RuleType.CONDITIONAL_REQUIRED, RuleType.CONDITIONAL_VALUE,
    RuleType.DEPENDENCY, RuleType.PATTERN, RuleType.RANGE,
}


def _conditional_value(rule: ValidationRule) -> str | None:
    return None if rule.allowed_values else "allowed_values is required"


def _dependency(rule: ValidationRule) -> str | None:
    if rule.conditions:
        return None
    return "conditions are required: they name what the target depends on"


def _mutual_exclusion(rule: ValidationRule) -> str | None:
    if len(rule.target_fields or []) >= 2:
        return None
    return "target_fields must name at least two fields"


def _pattern(rule: ValidationRule) -> str | None:
    if not rule.pattern:
        return "pattern is required"
    try:
        re.compile(rule.pattern)
    except re.error as e:
        return f"pattern does not compile: {e}"
    return None


def _range(rule: ValidationRule) -> str | None:
    bounds = [b for b in (rule.minimum, rule.maximum) if b is not None]
    if not bounds:
        return "minimum or maximum is required"
    if not all(math.isfinite(b) for b in bounds):
        return "minimum and maximum must be finite numbers"
    if len(bounds) == 2 and bounds[0] > bounds[1]:
        return "minimum is greater than maximum"
    return None


_TYPE_CHECKS: dict[RuleType, Callable[[ValidationRule], str | None]] = {
    RuleType.CONDITIONAL_VALUE: _conditional_value,
    RuleType.DEPENDENCY: _dependency,
    RuleType.MUTUAL_EXCLUSION: _mutual_exclusion,
    RuleType.PATTERN: _pattern,
    RuleType.RANGE: _range,
}


def rule_shape_error(rule: ValidationRule) -> str | None:
    """Why document-store could not evaluate this rule, or None.

    - conditional_required, conditional_value, dependency, pattern and
      range need a target_field
    - an 'in' / 'not_in' condition needs a list value
    - conditional_value needs allowed_values; dependency needs conditions
    - mutual_exclusion needs at least two target_fields
    - pattern needs a pattern that compiles
    - range needs a finite minimum or maximum, minimum <= maximum
    """
    if rule.type in _NEEDS_TARGET and not rule.target_field:
        return "target_field is required"
    for condition in rule.conditions:
        if condition.operator in ("in", "not_in") and not isinstance(condition.value, list):
            return (
                f"condition on '{condition.field}' with operator "
                f"'{condition.operator}' needs a list value"
            )
    check = _TYPE_CHECKS.get(rule.type)
    return check(rule) if check else None
