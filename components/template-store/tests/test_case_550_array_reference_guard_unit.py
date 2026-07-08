"""Unit tests for the CASE-550 array-reference guard on FieldDefinition.

An array_item_type='reference' field must declare a top-level reference_type,
otherwise the document-store collector queues its items with reference_type
None and reference resolution drops them silently — a bogus id validates clean.
The guard is a pure pydantic model validator, so these exercise it directly
against constructed FieldDefinition values — no MongoDB, no Registry, no HTTP.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from template_store.models.field import FieldDefinition, FieldType, ReferenceType


def _array_field(**kwargs) -> FieldDefinition:
    return FieldDefinition(
        name="kb_refs",
        label="KB Refs",
        type=FieldType.ARRAY,
        array_item_type=FieldType.REFERENCE,
        **kwargs,
    )


def test_array_reference_without_reference_type_is_rejected():
    with pytest.raises(ValidationError) as exc:
        _array_field()
    msg = str(exc.value)
    assert "reference_type is required" in msg
    assert "kb_refs" in msg


def test_array_reference_with_document_reference_type_is_accepted():
    f = _array_field(reference_type=ReferenceType.DOCUMENT)
    assert f.array_item_type == FieldType.REFERENCE
    assert f.reference_type == ReferenceType.DOCUMENT


def test_array_reference_with_term_reference_type_is_accepted():
    f = _array_field(reference_type=ReferenceType.TERM)
    assert f.reference_type == ReferenceType.TERM


def test_non_reference_array_needs_no_reference_type():
    """The guard only fires for array_item_type='reference' — a string array
    (or any other item type) is unaffected."""
    f = FieldDefinition(
        name="tags",
        label="Tags",
        type=FieldType.ARRAY,
        array_item_type=FieldType.STRING,
    )
    assert f.reference_type is None


def test_single_reference_field_still_needs_no_model_guard():
    """A single type='reference' field is unchanged by this guard — its
    reference_type is enforced at document validation, not model construction."""
    f = FieldDefinition(name="owner", label="Owner", type=FieldType.REFERENCE)
    assert f.reference_type is None
