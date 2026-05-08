"""Unit tests for the metadata-in-declarative-slots validator (CASE-317).

Exercises TemplateService._validate_no_metadata_in_declarative_slots
directly. Declarative slots commit the platform to a field's structural
meaning; metadata.* is caller-attached context with no schema
guarantees and must not appear in those slots.

Read-path queries (POST /documents/query filters) are deliberately NOT
covered here — they are not declarative slots and metadata.<x> filtering
remains supported. See document-store tests for the read-path coverage.
"""

from __future__ import annotations

import pytest

from template_store.models.field import FieldDefinition, FieldType
from template_store.services.template_service import TemplateService


def _field(
    name: str,
    *,
    type: FieldType = FieldType.STRING,
    full_text_indexed: bool | None = None,
) -> FieldDefinition:
    return FieldDefinition(
        name=name,
        label=name.title(),
        type=type,
        full_text_indexed=full_text_indexed,
    )


# =============================================================================
# identity_fields slot — must reject metadata.<x>
# =============================================================================


def test_identity_fields_rejects_metadata_path():
    """The exact pattern that motivated CASE-317: a template trying to
    declare structural identity against metadata.custom.<x>."""
    with pytest.raises(ValueError, match="identity_fields must reference"):
        TemplateService._validate_no_metadata_in_declarative_slots(
            ["metadata.custom.case_number"], [_field("title")]
        )


def test_identity_fields_rejects_bare_metadata_prefix():
    """Any path starting with `metadata.` is rejected — not just deep
    custom paths but also direct metadata.<top-level> references."""
    with pytest.raises(ValueError, match="identity_fields must reference"):
        TemplateService._validate_no_metadata_in_declarative_slots(
            ["metadata.source_system"], [_field("title")]
        )


def test_identity_fields_accepts_plain_field_name():
    """Plain field names (the documented convention) are accepted."""
    TemplateService._validate_no_metadata_in_declarative_slots(
        ["case_number"], [_field("case_number", type=FieldType.INTEGER)]
    )


def test_identity_fields_accepts_data_prefixed_path():
    """The data.<field> prefix is also accepted — same target, two
    notations both used in the codebase."""
    TemplateService._validate_no_metadata_in_declarative_slots(
        ["data.case_number"], [_field("case_number", type=FieldType.INTEGER)]
    )


def test_identity_fields_empty_passes():
    """Empty identity_fields is the platform's first-class append-only
    declaration per PoNIF #3 — must not raise."""
    TemplateService._validate_no_metadata_in_declarative_slots(
        [], [_field("event_type")]
    )


def test_identity_fields_none_passes():
    """None is treated the same as empty."""
    TemplateService._validate_no_metadata_in_declarative_slots(
        None, [_field("event_type")]
    )


def test_identity_fields_partial_metadata_reports_only_offenders():
    """Mixed identity_fields list: only the metadata-prefixed entries
    appear in the error, not the legitimate ones — diagnostic clarity."""
    with pytest.raises(ValueError) as excinfo:
        TemplateService._validate_no_metadata_in_declarative_slots(
            ["case_number", "metadata.custom.tag"], [_field("case_number")]
        )
    assert "metadata.custom.tag" in str(excinfo.value)
    assert "case_number" not in str(excinfo.value).split("Got")[1]


# =============================================================================
# full_text_indexed slot — must reject metadata-prefixed field names
# =============================================================================


def test_full_text_indexed_on_metadata_prefixed_field_rejected():
    """A field whose name itself starts with `metadata.` cannot carry
    full_text_indexed=true. metadata.* is not in the template schema
    and reporting-sync does not index it."""
    with pytest.raises(ValueError, match="full_text_indexed cannot be applied"):
        TemplateService._validate_no_metadata_in_declarative_slots(
            None,
            [_field("metadata.custom.notes", full_text_indexed=True)],
        )


def test_full_text_indexed_on_data_field_passes():
    """Plain field names with full_text_indexed=true are fine — the
    metadata guard only fires on `metadata.<x>` names."""
    TemplateService._validate_no_metadata_in_declarative_slots(
        None, [_field("body", full_text_indexed=True)]
    )


def test_full_text_indexed_false_on_metadata_field_passes():
    """A metadata-prefixed field WITHOUT full_text_indexed is not in
    a declarative slot — the validator only catches actual indexing
    declarations. (In practice, metadata-named fields are exotic and
    not produced by templates anyone ships, but the guard is defensive.)"""
    TemplateService._validate_no_metadata_in_declarative_slots(
        None,
        [_field("metadata.custom.notes", full_text_indexed=False)],
    )


# =============================================================================
# Combined — both identity_fields and fields populated
# =============================================================================


def test_clean_template_with_both_slots_populated_passes():
    """End-to-end happy path: identity_fields references real data
    fields, full_text_indexed is on a real string field, no metadata
    leakage anywhere."""
    fields = [
        _field("case_number", type=FieldType.INTEGER),
        _field("body", full_text_indexed=True),
    ]
    TemplateService._validate_no_metadata_in_declarative_slots(
        ["case_number"], fields
    )


def test_both_slots_violating_reports_identity_first():
    """When both slots have metadata leakage, identity_fields is the
    upstream check — it raises before fields are inspected. The order
    matters because identity is the more-fundamental contract."""
    with pytest.raises(ValueError, match="identity_fields"):
        TemplateService._validate_no_metadata_in_declarative_slots(
            ["metadata.custom.id"],
            [_field("metadata.custom.notes", full_text_indexed=True)],
        )
