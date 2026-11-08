"""Array-field validation, item type by item type, pinned before its refactor.

Provenance: CASE-1161. The suite reached almost none of
ValidationService._validate_array: a value that is not a list, and each
declared item type (term, object, string, number, integer, boolean, date,
datetime, file, reference). Each test calls the method directly and pins the
errors (code, field, message) or warnings it leaves on the result.
"""

from __future__ import annotations

from typing import Any

import pytest

from document_store.services import validation_service as vs
from document_store.services.template_store_client import TemplateStoreError
from document_store.services.validation_service import ValidationResult, ValidationService


async def _run(value: Any, **field: Any) -> ValidationResult:
    result = ValidationResult()
    await ValidationService()._validate_array(value, {"name": "f", "type": "array", **field}, "f", {}, result, {})
    return result


def _errors(result: ValidationResult) -> list[tuple[str, str, str]]:
    return [(e["code"], e["field"], e["message"]) for e in result.errors]


@pytest.mark.asyncio
async def test_a_value_that_is_not_a_list_is_refused():
    result = await _run("x", array_item_type="string")
    assert _errors(result) == [("invalid_type", "f", "Field 'f' must be an array")]


@pytest.mark.asyncio
async def test_untyped_items_are_not_checked():
    result = await _run([1, "a", None], array_item_type=None)
    assert result.errors == [] and result.warnings == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("item_type", "good", "bad", "what"), [
    ("string", ["a", ""], [1, None], "a string"),
    ("number", [1, 2.5], [True, "1"], "a number"),
    ("integer", [0, -3], [1.5, False], "an integer"),
    ("file", ["file-1"], [7, {"id": "x"}], "a string (file ID)"),
])
async def test_scalar_items_are_type_checked(item_type, good, bad, what):
    result = await _run(good + bad, array_item_type=item_type)
    first_bad = len(good)
    assert _errors(result) == [
        ("invalid_type", f"f[{i}]", f"Item at 'f[{i}]' must be {what}")
        for i in range(first_bad, first_bad + len(bad))
    ]


@pytest.mark.asyncio
async def test_boolean_date_and_datetime_items_use_the_field_validators():
    result = await _run([True, "yes"], array_item_type="boolean")
    assert [(e["code"], e["field"]) for e in result.errors] == [("invalid_type", "f[1]")]

    result = await _run(["2026-11-08", "08.11.2026"], array_item_type="date")
    assert _errors(result) == [
        ("invalid_type", "f[1]", "Field 'f[1]' must be a valid date (YYYY-MM-DD)"),
    ]

    result = await _run(["2026-11-08T10:00:00Z", "soon"], array_item_type="datetime")
    assert _errors(result) == [
        ("invalid_type", "f[1]", "Field 'f[1]' must be a valid datetime (ISO 8601)"),
    ]


@pytest.mark.asyncio
async def test_term_items_are_validated_as_term_fields_of_the_array_terminology(monkeypatch):
    calls: list[tuple] = []

    async def fake_term(self, item, field, path, template, result, validation):
        calls.append((item, field, path, validation))

    monkeypatch.setattr(ValidationService, "_validate_term", fake_term)
    await _run(["M", "F"], array_item_type="term", array_terminology_ref="GENDER")
    assert calls == [
        ("M", {"type": "term", "terminology_ref": "GENDER"}, "f[0]", {}),
        ("F", {"type": "term", "terminology_ref": "GENDER"}, "f[1]", {}),
    ]


class _Client:
    def __init__(self, answer: Any):
        self.answer = answer
        self.asked: list[tuple] = []

    async def get_template_resolved(self, ref, version=None):
        self.asked.append((ref, version))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.mark.asyncio
async def test_object_items_are_validated_against_the_pinned_item_template(monkeypatch):
    client = _Client({"fields": []})
    monkeypatch.setattr(vs, "get_template_store_client", lambda: client)
    seen: list[tuple] = []

    async def fake_fields(self, data, template, result, prefix=""):
        seen.append((data, template, prefix))

    monkeypatch.setattr(ValidationService, "_validate_fields", fake_fields)
    result = await _run(
        [{"a": 1}, "nope", {"b": 2}],
        array_item_type="object", array_template_ref="ADDR", array_template_ref_version=3,
    )
    assert _errors(result) == [("invalid_type", "f[1]", "Item at 'f[1]' must be an object")]
    assert client.asked == [("ADDR", 3), ("ADDR", 3)]
    assert seen == [({"a": 1}, {"fields": []}, "f[0]."), ({"b": 2}, {"fields": []}, "f[2].")]


@pytest.mark.asyncio
async def test_object_items_without_a_template_or_with_a_missing_one_are_not_checked(monkeypatch):
    client = _Client(None)
    monkeypatch.setattr(vs, "get_template_store_client", lambda: client)
    result = await _run([{"a": 1}], array_item_type="object")
    assert result.errors == [] and client.asked == []

    result = await _run([{"a": 1}], array_item_type="object", array_template_ref="ADDR")
    assert result.errors == [] and result.warnings == []
    assert client.asked == [("ADDR", None)]


@pytest.mark.asyncio
async def test_an_unreachable_item_template_is_a_warning(monkeypatch):
    monkeypatch.setattr(vs, "get_template_store_client", lambda: _Client(TemplateStoreError("down")))
    result = await _run([{"a": 1}, {"b": 2}], array_item_type="object", array_template_ref="ADDR")
    assert result.errors == []
    assert result.warnings == [
        "Could not validate array-item template 'ADDR' for field 'f[0]'",
        "Could not validate array-item template 'ADDR' for field 'f[1]'",
    ]


@pytest.mark.asyncio
async def test_reference_items_are_type_checked_by_reference_type():
    result = await _run(["doc-1", {"code": "x"}, 5], array_item_type="reference", reference_type="document")
    assert _errors(result) == [
        ("invalid_type", "f[2]", "Item at 'f[2]' must be a string or object (document reference)"),
    ]
    for ref_type in ("term", "terminology", "template"):
        result = await _run(["a", {"x": 1}], array_item_type="reference", reference_type=ref_type)
        assert _errors(result) == [
            ("invalid_type", "f[1]", f"Item at 'f[1]' must be a string ({ref_type} reference)"),
        ]
    # A grandfathered template without a reference_type: no per-item check.
    result = await _run([5, {"x": 1}], array_item_type="reference", reference_type=None)
    assert result.errors == []
