"""Unit tests for the full_text_indexed structural validator (Phase 1).

These exercise TemplateService._validate_full_text_indexed_constraints
directly against constructed FieldDefinition / ReportingConfig values —
no MongoDB, no Registry, no HTTP transport. Pure validator logic.

The HTTP-level tests in test_full_text_indexed.py exercise the same
constraints end-to-end (including pydantic coercion at the request
boundary) and require the dev stack to be up.
"""

from __future__ import annotations

import pytest

from template_store.models.field import FieldDefinition, FieldType
from template_store.models.template import ReportingConfig
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
# Backwards compatibility — no flags set
# =============================================================================


def test_no_indexed_fields_passes_with_no_reporting_config():
    fields = [_field("title"), _field("body")]
    # Should not raise.
    TemplateService._validate_full_text_indexed_constraints(fields, None)


def test_no_indexed_fields_passes_with_sync_disabled():
    fields = [_field("title"), _field("body")]
    # sync_enabled=False is fine when no field requests indexing.
    TemplateService._validate_full_text_indexed_constraints(
        fields, ReportingConfig(sync_enabled=False)
    )


# =============================================================================
# Happy path
# =============================================================================


def test_indexed_string_field_with_default_reporting_passes():
    fields = [_field("body", full_text_indexed=True)]
    TemplateService._validate_full_text_indexed_constraints(fields, None)


def test_indexed_string_field_with_explicit_sync_enabled_passes():
    fields = [_field("body", full_text_indexed=True)]
    TemplateService._validate_full_text_indexed_constraints(
        fields, ReportingConfig(sync_enabled=True)
    )


def test_full_text_indexed_false_is_treated_as_unset():
    """A False value is falsy and skips validation entirely."""
    fields = [
        _field("title"),
        _field("score", type=FieldType.NUMBER, full_text_indexed=False),
    ]
    # Even though score is not a string, full_text_indexed=False means
    # "not indexed" — no rejection.
    TemplateService._validate_full_text_indexed_constraints(fields, None)


# =============================================================================
# Type-rejection — only string fields can be indexed
# =============================================================================


@pytest.mark.parametrize(
    "field_type",
    [
        FieldType.NUMBER,
        FieldType.INTEGER,
        FieldType.BOOLEAN,
        FieldType.DATE,
        FieldType.DATETIME,
        FieldType.TERM,
        FieldType.REFERENCE,
        FieldType.FILE,
        FieldType.OBJECT,
        FieldType.ARRAY,
    ],
)
def test_indexed_non_string_field_rejected(field_type: FieldType):
    fields = [_field("offender", type=field_type, full_text_indexed=True)]
    with pytest.raises(ValueError) as exc:
        TemplateService._validate_full_text_indexed_constraints(fields, None)
    msg = str(exc.value)
    assert "full_text_indexed" in msg
    assert "string" in msg
    assert "offender" in msg
    assert field_type.value in msg


def test_indexed_mixed_types_reports_all_offenders():
    fields = [
        _field("title", full_text_indexed=True),  # OK
        _field("score", type=FieldType.NUMBER, full_text_indexed=True),  # not OK
        _field("active", type=FieldType.BOOLEAN, full_text_indexed=True),  # not OK
        _field("body", full_text_indexed=True),  # OK
    ]
    with pytest.raises(ValueError) as exc:
        TemplateService._validate_full_text_indexed_constraints(fields, None)
    msg = str(exc.value)
    assert "score" in msg
    assert "active" in msg
    # The OK fields shouldn't appear in the error.
    assert "title" not in msg
    assert "body" not in msg


# =============================================================================
# Sync-dependency — full_text_indexed requires reporting.sync_enabled=true
# =============================================================================


def test_indexed_field_with_sync_disabled_rejected():
    fields = [_field("body", full_text_indexed=True)]
    with pytest.raises(ValueError) as exc:
        TemplateService._validate_full_text_indexed_constraints(
            fields, ReportingConfig(sync_enabled=False)
        )
    msg = str(exc.value)
    assert "full_text_indexed" in msg
    assert "sync_enabled" in msg
    assert "body" in msg


def test_multiple_indexed_fields_with_sync_disabled_lists_all_in_error():
    fields = [
        _field("title", full_text_indexed=True),
        _field("body", full_text_indexed=True),
    ]
    with pytest.raises(ValueError) as exc:
        TemplateService._validate_full_text_indexed_constraints(
            fields, ReportingConfig(sync_enabled=False)
        )
    msg = str(exc.value)
    assert "title" in msg
    assert "body" in msg


def test_default_reporting_config_passes():
    """ReportingConfig() defaults to sync_enabled=True — must not reject."""
    fields = [_field("body", full_text_indexed=True)]
    TemplateService._validate_full_text_indexed_constraints(
        fields, ReportingConfig()
    )


# =============================================================================
# Schema reservation — ensure the field is present and accepts bool
# =============================================================================


def test_field_definition_default_is_none():
    f = FieldDefinition(name="x", label="X", type=FieldType.STRING)
    assert f.full_text_indexed is None


def test_field_definition_accepts_true_and_false():
    f_true = FieldDefinition(
        name="x", label="X", type=FieldType.STRING, full_text_indexed=True
    )
    assert f_true.full_text_indexed is True

    f_false = FieldDefinition(
        name="x", label="X", type=FieldType.STRING, full_text_indexed=False
    )
    assert f_false.full_text_indexed is False
