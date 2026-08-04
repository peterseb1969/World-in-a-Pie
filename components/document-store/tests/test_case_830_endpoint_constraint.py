"""CASE-830 — an edge type's endpoint enforcement must never fail permissive.

The generic reference validator treats an empty ``target_templates`` as "no
constraint" — correct for plain reference fields, catastrophic for an edge
type: the constraint is a projection derived from the declared endpoint
lists when template-store serves the template, so a template row that
reaches validation WITHOUT it means the projection was lost (a raw Mongo
row, a reader that did not derive). Accepting the write would silently drop
endpoint enforcement — the guardrail-that-works-sometimes shape.

This is the non-deriving-path refusal test the design requires: feed the
validator a stored-shape row directly, bypassing every deriving serializer,
and assert refusal rather than silent unconstrained acceptance.
"""

from __future__ import annotations

import pytest

from document_store.services.validation_service import (
    ValidationResult,
    ValidationService,
)


def _edge_template_row_without_projection() -> dict:
    """A relationship template as stored — declaration present, projection
    absent — i.e. what a reader sees when nothing derived the constraint."""
    return {
        "template_id": "0190aaaa-0000-7000-0000-000000000001",
        "namespace": "wip",
        "value": "MONSTER_HAS_SPELL",
        "usage": "relationship",
        "source_templates": [
            {"lookup_value": "MONSTER",
             "resolved": "0190aaaa-0000-7000-0000-000000000002"}
        ],
        "target_templates": [
            {"lookup_value": "SPELL",
             "resolved": "0190aaaa-0000-7000-0000-000000000003"}
        ],
        "fields": [
            {"name": "source_ref", "type": "reference",
             "reference_type": "document", "mandatory": True,
             "target_templates": []},
            {"name": "target_ref", "type": "reference",
             "reference_type": "document", "mandatory": True,
             "target_templates": []},
        ],
    }


@pytest.mark.asyncio
async def test_edge_ref_field_without_constraint_is_refused():
    service = ValidationService()
    result = ValidationResult()
    data = {
        "source_ref": "0190bbbb-0000-7000-0000-000000000001",
        "target_ref": "0190bbbb-0000-7000-0000-000000000002",
    }

    await service._validate_references(
        data, _edge_template_row_without_projection(), result, "wip"
    )

    codes = [e.get("code") for e in result.errors]
    assert codes.count("missing_endpoint_constraint") == 2, result.errors
    assert not result.valid
    # Nothing was resolved or accepted for the unconstrained endpoints.
    assert result.references == []


@pytest.mark.asyncio
async def test_plain_reference_field_keeps_permissive_empty():
    """The refusal is edge-endpoint-scoped: an unconstrained reference field
    on an ENTITY template remains a feature, not an error."""
    service = ValidationService()
    result = ValidationResult()
    row = _edge_template_row_without_projection()
    row["usage"] = "entity"
    row["source_templates"] = []
    row["target_templates"] = []
    # Rename so the fields read as ordinary references on an entity.
    row["fields"] = [
        {"name": "anything_ref", "type": "reference",
         "reference_type": "document", "target_templates": []},
    ]

    await service._validate_references(
        {"anything_ref": "0190bbbb-0000-7000-0000-00000000cafe"},
        row, result, "wip",
    )

    codes = [e.get("code") for e in result.errors]
    assert "missing_endpoint_constraint" not in codes
