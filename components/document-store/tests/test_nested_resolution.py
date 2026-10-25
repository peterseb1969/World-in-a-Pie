"""Terms, references and files inside nested templates are resolved like top-level ones.

Stage 3 already walked into object fields with a template_ref and into the
items of object arrays with an array_template_ref, but for a term, reference
or file field it only checks the value's type: the real checks run in the
term, reference and file stages. Those stages used to collect only top-level
fields and arrays of scalars, so inside a nested object an unknown term was
accepted, a reference was never existence-checked, and no term_references
were recorded. These tests run the full pipeline against a parent template
whose nested template (pinned v1) carries a term, a reference and a file.
"""

from unittest.mock import AsyncMock

import pytest

from document_store.services.document_service import get_document_service
from document_store.services.validation_service import ValidationResult

from .conftest import SAMPLE_TEMPLATES, VERSIONED_TEMPLATE_OVERRIDES

LINE = "LINE_NESTED_RESOLUTION"


def _line_schema(version: int) -> dict:
    fields = [
        {"name": "label", "label": "Label", "type": "string"},
        {"name": "country", "label": "Country", "type": "term", "terminology_ref": "COUNTRY"},
        {"name": "owner", "label": "Owner", "type": "reference", "reference_type": "document"},
        {"name": "scan", "label": "Scan", "type": "file", "file_config": {"max_size_mb": 5}},
    ]
    if version == 2:
        # v2 drops the term field: a walker that ignored the pin would see no term.
        fields = [f for f in fields if f["name"] != "country"]
    return {
        "template_id": LINE, "value": LINE, "version": version, "status": "active",
        "namespace": "wip", "identity_fields": [], "fields": fields, "rules": [],
    }


@pytest.fixture(autouse=True)
def _line_templates():
    SAMPLE_TEMPLATES[LINE] = _line_schema(2)
    VERSIONED_TEMPLATE_OVERRIDES[(LINE, 1)] = _line_schema(1)
    VERSIONED_TEMPLATE_OVERRIDES[(LINE, 2)] = _line_schema(2)
    yield
    SAMPLE_TEMPLATES.pop(LINE, None)
    VERSIONED_TEMPLATE_OVERRIDES.clear()


PARENT = {
    "template_id": "PARENT_NESTED_RESOLUTION", "value": "PARENT_NESTED_RESOLUTION",
    "version": 1, "status": "active", "namespace": "wip", "identity_fields": [],
    "rules": [],
    "fields": [
        {"name": "lines", "label": "Lines", "type": "array", "array_item_type": "object",
         "array_template_ref": LINE, "array_template_ref_version": 1},
        {"name": "home", "label": "Home", "type": "object",
         "template_ref": LINE, "template_ref_version": 1},
    ],
}


async def validate(data: dict) -> ValidationResult:
    vs = get_document_service().validation_service
    return await vs.validate("unused", data, namespace="wip", template_override=PARENT)


def term_paths(result: ValidationResult) -> list[str]:
    return sorted(t["field_path"] for t in result.term_references)


@pytest.mark.asyncio
async def test_unknown_term_in_object_array_item_is_rejected(client, auth_headers):
    result = await validate({"lines": [{"country": "USA"}, {"country": "ATLANTIS"}]})
    invalid = [e for e in result.errors if e["code"] == "invalid_term"]
    assert [e["field"] for e in invalid] == ["lines[1].country"]


@pytest.mark.asyncio
async def test_object_array_item_terms_are_recorded(client, auth_headers):
    result = await validate({"lines": [{"country": "USA"}, {"label": "x"}, {"country": "UK"}]})
    assert result.valid, result.errors
    assert term_paths(result) == ["lines[0].country", "lines[2].country"]


@pytest.mark.asyncio
async def test_nested_object_term_is_resolved_and_checked(client, auth_headers):
    assert term_paths(await validate({"home": {"country": "CA"}})) == ["home.country"]
    bad = await validate({"home": {"country": "ATLANTIS"}})
    assert [e["field"] for e in bad.errors if e["code"] == "invalid_term"] == ["home.country"]


@pytest.mark.asyncio
async def test_nested_template_is_read_at_its_pinned_version(client, auth_headers):
    """v1 carries the term field and v2 does not; the parent pins v1."""
    result = await validate({"lines": [{"country": "ATLANTIS"}]})
    assert any(e["code"] == "invalid_term" for e in result.errors)


@pytest.mark.asyncio
async def test_nested_reference_is_existence_checked(client, auth_headers, monkeypatch):
    vs = get_document_service().validation_service

    async def _miss(value, target_templates, result, field_path, **kwargs):
        result.add_error(code="reference_not_found", message=f"Reference '{value}' not found",
                         field=field_path)
        return None

    monkeypatch.setattr(vs, "_resolve_document_reference", AsyncMock(side_effect=_miss))
    result = await validate({"lines": [{"owner": "bogus-id"}], "home": {"owner": "also-bogus"}})
    not_found = {e["field"] for e in result.errors if e["code"] == "reference_not_found"}
    assert not_found == {"lines[0].owner", "home.owner"}


@pytest.mark.asyncio
async def test_nested_file_is_collected(client, auth_headers):
    vs = get_document_service().validation_service
    collected = await vs._collect_file_values(
        {"lines": [{"scan": "FILE-1"}]}, PARENT["fields"], ""
    )
    assert [(c["field_path"], c["file_id"]) for c in collected] == [("lines[0].scan", "FILE-1")]


@pytest.mark.asyncio
async def test_unfetchable_nested_template_does_not_break_collection(client, auth_headers):
    vs = get_document_service().validation_service
    fields = [{"name": "x", "type": "object", "template_ref": "NO_SUCH_TEMPLATE",
               "template_ref_version": 1}]
    assert await vs._collect_term_values({"x": {"country": "USA"}}, fields, "") == []
