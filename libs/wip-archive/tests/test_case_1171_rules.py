"""The offline validator evaluates every rule type the template model accepts.

It evaluated only conditional_required, conditional_value, mutual_exclusion
and dependency: a ``pattern`` or ``range`` rule passed anything offline that a
live write refuses, and an unknown or unevaluable rule was silently skipped.
It now evaluates both and refuses an unevaluable rule per document
(``invalid_rule``), as document-store does.
"""

from wip_archive.definitions import DefinitionIndex
from wip_archive.validation import OfflineValidator

NS = "demo"
TPL = "0190c000-0000-7000-0000-0000000001171"
FIELDS = [{"name": "age", "type": "number"}, {"name": "code", "type": "string"},
          {"name": "kind", "type": "string"}]


def _check(rules, data):
    template = {"template_id": TPL, "version": 1, "value": "ITEM", "namespace": NS,
                "identity_fields": [], "fields": FIELDS, "rules": rules}
    return OfflineValidator(DefinitionIndex()).validate(template, data, NS)


def _codes(outcome):
    return {e["code"] for e in outcome.errors}


AGE = {"type": "range", "target_field": "age", "minimum": 0, "maximum": 150}
CODE = {"type": "pattern", "target_field": "code", "pattern": "[A-Z]{3}"}


def test_a_range_rule_holds():
    assert _codes(_check([AGE], {"age": 900})) == {"range"}
    assert _check([AGE], {"age": 150}).valid
    assert _check([AGE], {}).valid


def test_a_pattern_rule_matches_the_whole_value():
    assert _check([CODE], {"code": "ABC"}).valid
    assert _codes(_check([CODE], {"code": "ABC-1"})) == {"pattern"}


def test_a_rule_applies_only_when_its_conditions_hold():
    rule = {**AGE, "conditions": [{"field": "kind", "operator": "equals", "value": "person"}]}
    assert _check([rule], {"kind": "rock", "age": 900}).valid
    assert _codes(_check([rule], {"kind": "person", "age": 900})) == {"range"}


def test_a_rule_that_cannot_be_evaluated_is_refused():
    assert _codes(_check([{"type": "sometimes"}], {"age": 1})) == {"invalid_rule"}
    assert _codes(_check([{"type": "pattern", "target_field": "code", "pattern": "("}],
                         {"code": "x"})) == {"invalid_rule"}
