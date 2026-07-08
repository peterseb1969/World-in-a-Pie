"""CASE-550 — array-of-references are existence-validated, not silently skipped.

`array_item_type: reference` fields queue each item for reference resolution
using the field's top-level `reference_type`. Two halves are exercised here:

1. Stage-3 per-item type checking (`_validate_array`'s reference branch) —
   a document-ref array rejects a non-string/non-dict item; the others reject
   non-strings; a grandfathered field with no `reference_type` is left alone.
2. The collect→resolve wiring (`_collect_reference_values` + `_validate_references`)
   — array items are queued with the field's `reference_type` and a per-item
   field path, and a bogus id surfaces `reference_not_found` on `kb_refs[i]`
   (the exact leak the case reported: previously silent, `references: []`).

Stage-1 checks are pure isinstance logic (no I/O). The resolution test mocks
`_resolve_document_reference` so no Registry/Mongo is required, matching
test_case_435's pattern.
"""

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from document_store.services.document_service import get_document_service
from document_store.services.validation_service import ValidationResult


def _doc_ref_array_field() -> dict:
    return {
        "name": "kb_refs",
        "type": "array",
        "array_item_type": "reference",
        "reference_type": "document",
        "target_templates": ["ARTICLE"],
    }


# ---------------------------------------------------------------------------
# Stage-3: _validate_array reference branch (pure type checks)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestArrayReferenceTypeChecks:
    async def test_document_ref_array_rejects_non_string_item(
        self, client: AsyncClient, auth_headers: dict
    ):
        vs = get_document_service().validation_service
        result = ValidationResult()
        await vs._validate_array(
            value=["019abc00-0000-7000-8000-000000000000", 42],
            field=_doc_ref_array_field(),
            field_path="kb_refs",
            template={},
            result=result,
            validation={},
        )
        bad = [e for e in result.errors if e["code"] == "invalid_type"]
        assert len(bad) == 1
        assert bad[0]["field"] == "kb_refs[1]"

    async def test_document_ref_array_accepts_string_and_dict(
        self, client: AsyncClient, auth_headers: dict
    ):
        vs = get_document_service().validation_service
        result = ValidationResult()
        await vs._validate_array(
            value=["019abc00-0000-7000-8000-000000000000", {"slug": "x"}],
            field=_doc_ref_array_field(),
            field_path="kb_refs",
            template={},
            result=result,
            validation={},
        )
        assert not result.errors

    async def test_term_ref_array_rejects_non_string(
        self, client: AsyncClient, auth_headers: dict
    ):
        vs = get_document_service().validation_service
        result = ValidationResult()
        field = {
            "name": "codes",
            "type": "array",
            "array_item_type": "reference",
            "reference_type": "term",
        }
        await vs._validate_array(
            value=[{"not": "a string"}],
            field=field,
            field_path="codes",
            template={},
            result=result,
            validation={},
        )
        assert any(
            e["code"] == "invalid_type" and e["field"] == "codes[0]"
            for e in result.errors
        )

    async def test_grandfathered_field_without_reference_type_is_left_alone(
        self, client: AsyncClient, auth_headers: dict
    ):
        """A template that predates the template-store guard carries no
        reference_type; the Stage-3 branch imposes no per-item type constraint
        so its existing soft-link data keeps validating."""
        vs = get_document_service().validation_service
        result = ValidationResult()
        field = {
            "name": "kb_refs",
            "type": "array",
            "array_item_type": "reference",
            # no reference_type — grandfathered
        }
        await vs._validate_array(
            value=[42, {"anything": True}, "x"],
            field=field,
            field_path="kb_refs",
            template={},
            result=result,
            validation={},
        )
        assert not result.errors


# ---------------------------------------------------------------------------
# Collect + resolve wiring: array items are existence-checked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestArrayReferenceResolution:
    async def test_collector_queues_each_item_with_reference_type(
        self, client: AsyncClient, auth_headers: dict
    ):
        vs = get_document_service().validation_service
        fields = [_doc_ref_array_field()]
        data = {"kb_refs": ["id-a", "id-b"]}
        collected = vs._collect_reference_values(data, fields, "")
        assert [c["field_path"] for c in collected] == ["kb_refs[0]", "kb_refs[1]"]
        assert all(c["reference_type"] == "document" for c in collected)
        assert all(c["target_templates"] == ["ARTICLE"] for c in collected)

    async def test_bogus_id_in_array_surfaces_reference_not_found(
        self, client: AsyncClient, auth_headers: dict, monkeypatch
    ):
        """The leak the case reported: a non-existent id in an array-of-refs
        previously validated clean. It must now surface reference_not_found on
        the offending per-item path."""
        vs = get_document_service().validation_service
        # Registry/Mongo resolution reports a miss (adds the error + returns None).
        async def _miss(value, target_templates, result, field_path, **kwargs):
            result.add_error(
                code="reference_not_found",
                message=f"Reference '{value}' not found",
                field=field_path,
            )
            return None

        monkeypatch.setattr(vs, "_resolve_document_reference", AsyncMock(side_effect=_miss))

        result = ValidationResult()
        template = {"fields": [_doc_ref_array_field()]}
        data = {"kb_refs": ["real-id", "bogus-id"]}
        await vs._validate_references(data, template, result, namespace="wip")

        not_found = [e for e in result.errors if e["code"] == "reference_not_found"]
        assert {e["field"] for e in not_found} == {"kb_refs[0]", "kb_refs[1]"}
