"""Unit tests for the versioned:false ⇒ identity_fields validator (CASE-478).

These exercise TemplateService._validate_versioned_requires_identity directly —
no MongoDB, no Registry, no HTTP transport. Pure validator logic.

Design (CASE-478): a template with `versioned: false` overwrites in place on
update, addressed by its identity. With no identity_fields there is nothing to
address — and PATCH is rejected too (append_only) — so the combination is
incoherent. Enforced on both create and update; this unit covers the shared
validator both call.
"""

from __future__ import annotations

import pytest

from template_store.services.template_service import TemplateService

# =============================================================================
# Rejected: versioned:false + no identity
# =============================================================================


def test_versioned_false_empty_identity_raises():
    with pytest.raises(ValueError, match="versioned:false requires identity_fields"):
        TemplateService._validate_versioned_requires_identity(False, [])


def test_versioned_false_none_identity_raises():
    # None is falsy — same incoherent state as the empty list.
    with pytest.raises(ValueError, match="versioned:false requires identity_fields"):
        TemplateService._validate_versioned_requires_identity(False, None)


def test_rejection_message_names_the_fix():
    # The error must teach: it names identity_fields and the versioned:true escape.
    with pytest.raises(ValueError) as exc:
        TemplateService._validate_versioned_requires_identity(False, [])
    msg = str(exc.value)
    assert "identity_fields" in msg
    assert "versioned:true" in msg


# =============================================================================
# Allowed
# =============================================================================


def test_versioned_false_with_identity_passes():
    # Edge types (the canonical versioned:false case) carry identity.
    TemplateService._validate_versioned_requires_identity(
        False, ["source_ref", "target_ref"]
    )


def test_versioned_true_empty_identity_passes():
    # Append-only mode: empty identity_fields IS the declaration, and it is
    # versioned:true (the default). PATCH is rejected at the doc layer, not here.
    TemplateService._validate_versioned_requires_identity(True, [])


def test_versioned_true_with_identity_passes():
    TemplateService._validate_versioned_requires_identity(True, ["case_number"])
