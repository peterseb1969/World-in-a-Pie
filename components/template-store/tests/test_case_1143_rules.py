"""Template-store refuses a rule document-store could not evaluate.

Provenance: CASE-1143. A rule that names no field, a condition 'in' a single
value, or a pattern that does not compile used to be stored, and then either
crashed document validation or was skipped there.
"""

import pytest
from httpx import AsyncClient

from template_store.models.rule import ValidationRule
from template_store.services.errors import TemplateError
from template_store.services.template_service import TemplateService

GOOD = [
    {"type": "conditional_required", "target_field": "b",
     "conditions": [{"field": "a", "operator": "in", "value": ["x"]}]},
    {"type": "conditional_value", "target_field": "b", "allowed_values": ["p"]},
    {"type": "mutual_exclusion", "target_fields": ["a", "b"]},
    {"type": "dependency", "target_field": "b",
     "conditions": [{"field": "a", "operator": "exists"}]},
    {"type": "pattern", "target_field": "a", "pattern": "[A-Z]{3}"},
    {"type": "range", "target_field": "n", "minimum": 0},
    {"type": "range", "target_field": "n", "minimum": 0, "maximum": 0},
]

BAD = [
    ({"type": "conditional_required",
      "conditions": [{"field": "a", "operator": "exists"}]}, "target_field is required"),
    ({"type": "conditional_required", "target_field": "b",
      "conditions": [{"field": "a", "operator": "in", "value": 5}]}, "needs a list value"),
    ({"type": "conditional_required", "target_field": "b",
      "conditions": [{"field": "a", "operator": "not_in", "value": "abc"}]}, "needs a list value"),
    ({"type": "conditional_value", "target_field": "b"}, "allowed_values is required"),
    ({"type": "dependency", "target_field": "b"}, "conditions are required"),
    ({"type": "mutual_exclusion", "target_fields": ["a"]}, "at least two"),
    ({"type": "pattern", "target_field": "a"}, "pattern is required"),
    ({"type": "pattern", "target_field": "a", "pattern": "[unclosed"}, "does not compile"),
    ({"type": "range", "target_field": "n"}, "minimum or maximum is required"),
    ({"type": "range", "target_field": "n", "minimum": 5, "maximum": 1}, "greater than maximum"),
    ({"type": "range", "target_field": "n", "maximum": float("inf")}, "finite"),
]


def test_every_evaluable_rule_shape_passes():
    TemplateService.validate_rules_for_write([ValidationRule(**r) for r in GOOD])


@pytest.mark.parametrize(("rule", "why"), BAD)
def test_a_rule_document_store_cannot_evaluate_is_refused(rule, why):
    with pytest.raises(TemplateError) as exc:
        TemplateService.validate_rules_for_write([ValidationRule(**GOOD[0]), ValidationRule(**rule)])
    assert exc.value.code == "validation_failed"
    assert exc.value.details == {"rule": "invalid_rule"}
    assert "Rule 1 " in str(exc.value) and why in str(exc.value)


def _template(value: str, rules: list[dict]) -> dict:
    return {
        "namespace": "wip", "value": value, "label": value, "identity_fields": ["a"],
        "fields": [
            {"name": "a", "label": "A", "type": "string"},
            {"name": "b", "label": "B", "type": "string"},
        ],
        "rules": rules,
    }


@pytest.mark.asyncio
async def test_create_and_update_refuse_the_rule_per_item(client: AsyncClient, auth_headers: dict):
    url = "/api/template-store/templates"
    bad = [{"type": "dependency", "conditions": [{"field": "a", "operator": "exists"}]}]

    resp = await client.post(url, headers=auth_headers, json=[
        _template("RULES_1143_BAD", bad), _template("RULES_1143_OK", GOOD[:1]),
    ])
    assert resp.status_code == 200
    first, second = resp.json()["results"]
    assert (first["status"], first["error_code"]) == ("error", "validation_failed")
    assert first["details"]["rule"] == "invalid_rule"
    assert second["status"] == "created"

    # The same template re-posted with the bad rule is an update: refused too.
    resp = await client.post(url, headers=auth_headers, json=[_template("RULES_1143_OK", bad)])
    item = resp.json()["results"][0]
    assert (item["status"], item["details"]["rule"]) == ("error", "invalid_rule")


@pytest.mark.asyncio
@pytest.mark.parametrize(("rule", "where"), [
    ({"type": "conditional_required", "target_field": "b",
      "condition": {"field": "a", "operator": "exists"}}, "rules[0]"),
    ({"type": "conditional_required", "target_field": "b",
      "conditions": [{"field": "a", "operator": "exists", "values": ["x"]}]}, "rules[0].conditions[0]"),
])
async def test_an_unknown_rule_key_is_refused_not_dropped(
    client: AsyncClient, auth_headers: dict, rule: dict, where: str,
):
    """Dropped, `condition` (singular) would turn the rule unconditional."""
    resp = await client.post(
        "/api/template-store/templates", headers=auth_headers,
        json=[_template("RULES_1143_KEYS", [rule])],
    )
    assert resp.status_code == 422
    assert where in resp.text and "unknown key" in resp.text
