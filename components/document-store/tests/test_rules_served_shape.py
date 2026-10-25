"""Cross-field rules evaluate the shape template-store actually serves.

Template-store serialises every optional rule key, unset ones as null
(`"required": null`, `"allowed_values": null`, ...). A reader that falls back
only when a key is ABSENT treats those nulls as real values: a
conditional_required rule then reads "not required" and never fires, and the
list-valued keys raise. These tests feed the evaluator rules in the served
shape rather than hand-written dicts that omit the optional keys.
"""

from document_store.services.validation_service import ValidationResult, ValidationService


def served(rule: dict) -> dict:
    """A rule as template-store serves it: every optional key present."""
    shape = {
        "description": None,
        "conditions": [],
        "target_field": None,
        "target_fields": None,
        "required": None,
        "allowed_values": None,
        "pattern": None,
        "minimum": None,
        "maximum": None,
        "error_message": None,
    }
    return {**shape, **rule}


def evaluate(data: dict, *rules: dict) -> ValidationResult:
    result = ValidationResult()
    ValidationService()._evaluate_rules(data, {"rules": list(rules)}, result)
    return result


WEB_IMPORT_NEEDS_URL = served({
    "type": "conditional_required",
    "conditions": [{"field": "source_type", "operator": "equals", "value": "web_import"}],
    "target_field": "source_url",
})


def codes(result: ValidationResult) -> list[str]:
    return [e["code"] for e in result.errors]


def test_conditional_required_with_null_required_fires():
    result = evaluate({"source_type": "web_import"}, WEB_IMPORT_NEEDS_URL)
    assert codes(result) == ["conditional_required"]


def test_conditional_required_satisfied():
    result = evaluate({"source_type": "web_import", "source_url": "https://x"}, WEB_IMPORT_NEEDS_URL)
    assert result.valid


def test_conditional_required_condition_not_met():
    assert evaluate({"source_type": "manual"}, WEB_IMPORT_NEEDS_URL).valid


def test_conditional_required_explicit_false_does_not_fire():
    rule = {**WEB_IMPORT_NEEDS_URL, "required": False}
    assert evaluate({"source_type": "web_import"}, rule).valid


def test_conditional_value_with_null_allowed_values_does_not_raise():
    rule = served({
        "type": "conditional_value",
        "conditions": [{"field": "a", "operator": "exists"}],
        "target_field": "b",
    })
    assert codes(evaluate({"a": 1, "b": 2}, rule)) == ["conditional_value"]


def test_mutual_exclusion_with_null_target_fields_does_not_raise():
    rule = served({"type": "mutual_exclusion"})
    assert evaluate({"a": 1, "b": 2}, rule).valid


def test_null_conditions_and_rules_do_not_raise():
    rule = {**WEB_IMPORT_NEEDS_URL, "conditions": None}
    # No conditions means the rule always applies.
    assert codes(evaluate({}, rule)) == ["conditional_required"]
    result = ValidationResult()
    ValidationService()._evaluate_rules({}, {"rules": None}, result)
    assert result.valid
