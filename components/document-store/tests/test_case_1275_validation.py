"""Constraints a template declares and validation quietly skipped.

Provenance: CASE-1275 (commit-review batch 14: 0ba6bfb2 R1/R2/R3,
104b7359 R2/R3, plus the enum and array-file-item lows).

- A rule of a shape template-store refuses at write time is refused per
  document (`invalid_rule`), never skipped: a range without bounds, a
  conditional_value without allowed_values, a mutual_exclusion naming fewer
  than two fields, a dependency without conditions.
- A rule element that is not an object is refused, not a 500.
- `mutual_exclusion` applies only when its conditions hold, like every other
  rule type.
- A declared `enum` holds for date and datetime too, and an enum declared
  empty admits nothing.
- `array_file_config.max_files` caps an array of files.
- An array with no item type has untyped items — on an inline candidate as on
  a stored template.
"""

from unittest.mock import AsyncMock

import pytest

CANDIDATE = "/api/document-store/validation/validate-candidate"


def _definition(fields: list[dict], rules: list[dict] | None = None) -> dict:
    return {
        "template_id": "candidate", "value": "CANDIDATE_1275", "label": "C",
        "version": 1, "status": "active", "namespace": "wip",
        "identity_fields": [], "fields": fields, "rules": rules or [],
    }


async def _codes(client, auth_headers, fields, documents, rules=None):
    resp = await client.post(CANDIDATE, headers=auth_headers, json={
        "namespace": "wip",
        "template_definition": _definition(fields, rules),
        "documents": documents,
    })
    assert resp.status_code == 200, resp.text
    return [[e["code"] for e in r["validation"]["errors"]] for r in resp.json()["results"]], [
        r["validation"]["errors"] for r in resp.json()["results"]
    ]


@pytest.mark.asyncio
async def test_a_rule_template_store_would_refuse_is_refused_not_skipped(
    client, auth_headers
):
    """Every shape rule_shape.py refuses, in one document: each is an error."""
    bad_rules = [
        {"type": "range", "target_field": "n"},                    # no bound
        {"type": "conditional_value", "target_field": "s"},         # no allowed_values
        {"type": "mutual_exclusion", "target_fields": ["a"]},       # fewer than two
        {"type": "dependency", "target_field": "a"},                 # no conditions
    ]
    codes, errors = await _codes(
        client, auth_headers,
        [{"name": "n", "label": "n", "type": "integer"},
         {"name": "s", "label": "s", "type": "string"},
         {"name": "a", "label": "a", "type": "string"}],
        [{"n": 1, "s": "x", "a": "y"}],
        bad_rules,
    )
    assert codes[0] == ["invalid_rule"] * 4
    assert [e["details"]["rule_index"] for e in errors[0]] == [0, 1, 2, 3]
    assert [e["details"]["rule_type"] for e in errors[0]] == [
        "range", "conditional_value", "mutual_exclusion", "dependency",
    ]
    assert all("fix the template's rules" in e["message"] for e in errors[0])


@pytest.mark.asyncio
async def test_a_rule_that_is_not_an_object_is_refused_instead_of_500(
    client, auth_headers
):
    codes, errors = await _codes(
        client, auth_headers,
        [{"name": "a", "label": "a", "type": "string"}],
        [{"a": "y"}],
        ["a rule is an object"],
    )
    assert codes[0] == ["invalid_rule"]
    assert "must be an object" in errors[0][0]["message"]


@pytest.mark.asyncio
async def test_mutual_exclusion_applies_only_when_its_conditions_hold(client, auth_headers):
    rule = {
        "type": "mutual_exclusion", "target_fields": ["a", "b"],
        "conditions": [{"field": "mode", "operator": "equals", "value": "exclusive"}],
    }
    fields = [{"name": n, "label": n, "type": "string"} for n in ("mode", "a", "b")]
    codes, _ = await _codes(client, auth_headers, fields, [
        {"mode": "exclusive", "a": "1", "b": "2"},
        {"mode": "both_allowed", "a": "1", "b": "2"},
        {"mode": "exclusive", "a": "1"},
    ], [rule])
    assert codes[0] == ["mutual_exclusion"]
    assert codes[1] == []
    assert codes[2] == []


@pytest.mark.asyncio
async def test_rules_that_can_be_evaluated_still_evaluate(client, auth_headers):
    """The shape check refuses what cannot be evaluated, not what fails."""
    rules = [
        {"type": "range", "target_field": "n", "minimum": 1, "maximum": 3},
        {"type": "conditional_value", "target_field": "s", "allowed_values": ["ok"]},
        {"type": "mutual_exclusion", "target_fields": ["a", "b"]},
        {"type": "dependency", "target_field": "s",
         "conditions": [{"field": "a", "operator": "exists"}]},
    ]
    fields = [{"name": n, "label": n, "type": "string"} for n in ("s", "a", "b")] + [
        {"name": "n", "label": "n", "type": "integer"}
    ]
    codes, _ = await _codes(client, auth_headers, fields, [
        {"n": 2, "s": "ok", "a": "1"},                       # every rule satisfied
        {"n": 9, "s": "no", "a": "1", "b": "2"},             # range, value, exclusion
        {"n": 2, "s": "ok"},                                 # dependency unmet
    ], rules)
    assert codes[0] == []
    assert codes[1] == ["range", "conditional_value", "mutual_exclusion"]
    assert codes[2] == ["dependency"]


@pytest.mark.asyncio
async def test_enum_holds_for_date_and_datetime(client, auth_headers):
    fields = [
        {"name": "d", "label": "d", "type": "date", "validation": {"enum": ["2024-01-01"]}},
        {"name": "t", "label": "t", "type": "datetime",
         "validation": {"enum": ["2024-01-01T10:00:00Z"]}},
    ]
    codes, _ = await _codes(client, auth_headers, fields, [
        {"d": "2024-01-01", "t": "2024-01-01T10:00:00Z"},
        {"d": "2024-06-06", "t": "2024-06-06T10:00:00Z"},
    ])
    assert codes[0] == []
    assert codes[1] == ["invalid_enum", "invalid_enum"]


@pytest.mark.asyncio
async def test_a_declared_empty_enum_admits_nothing(client, auth_headers):
    """An empty whitelist is a constraint that refuses, not 'no constraint'."""
    fields = [{"name": "s", "label": "s", "type": "string", "validation": {"enum": []}}]
    codes, _ = await _codes(client, auth_headers, fields, [{"s": "anything"}])
    assert codes[0] == ["invalid_enum"]


@pytest.mark.asyncio
async def test_array_file_config_caps_an_array_of_files(client, auth_headers):
    fields = [{"name": "f", "label": "f", "type": "array", "array_item_type": "file",
               "array_file_config": {"max_files": 2}}]
    codes, _ = await _codes(client, auth_headers, fields, [
        {"f": ["a", "b"]},
        {"f": ["a", "b", "c"]},
    ])
    assert codes[0] == []
    assert codes[1] == ["max_files"]


@pytest.mark.asyncio
async def test_an_array_without_an_item_type_has_untyped_items(client, auth_headers):
    """No default item type is declared, so nothing is checked per item — the
    same reading for an inline candidate and for a stored template."""
    fields = [{"name": "a", "label": "a", "type": "array"}]
    codes, _ = await _codes(client, auth_headers, fields, [{"a": [1, "x", True, None]}])
    assert codes[0] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ref", ["COUNTRY", "other:COUNTRY", "019469a0-cccc-7abc-8def-000000000001"])
async def test_a_missed_terminology_ref_names_the_namespace_it_searched(monkeypatch, ref):
    """A bare value resolves in the document's own namespace only, so the
    refusal says where it looked and how to name another namespace's copy —
    the distinction the resolve layer draws. A qualified name or an id is not
    under-specified, so it gets no such hint."""
    from document_store.services import validation_service as mod
    from document_store.services.validation_service import ValidationResult, ValidationService

    fake = AsyncMock()
    fake.get_terminology = AsyncMock(return_value=None)
    monkeypatch.setattr(mod, "get_def_store_client", lambda: fake)

    result = ValidationResult()
    assert await ValidationService()._resolve_terminology_reference(
        ref, result, "country", "acme",
    ) is None
    assert result.errors[0]["code"] == "invalid_terminology_reference"
    message = result.errors[0]["message"]
    assert f"Terminology '{ref}' not found in namespace 'acme'" == message.split(
        ". A bare value"
    )[0]
    assert ("A bare value resolves in 'acme' only" in message) is (ref == "COUNTRY")
