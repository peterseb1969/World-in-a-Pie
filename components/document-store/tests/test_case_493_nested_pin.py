"""CASE-493 — nested template_ref validation resolves the PINNED version, not latest.

The bug: nested-object (`template_ref`) / array (`array_template_ref`) data was
validated against the *latest active* version of the nested template, so a
parent document could strand on PATCH once that nested template shipped an
incompatible new version. The fix pins every nested reference to an explicit
version. These tests prove the resolver honours the pin:

- A parent whose nested data is valid against the pinned v1 validates cleanly
  even though an incompatible v2 (adds a mandatory field) exists — a "latest"
  resolution would wrongly fail it.
- Control: pinning the same field to v2 DOES enforce v2, confirming the version
  argument actually selects the schema (the assertion isn't vacuous).
"""

import pytest

from document_store.services.document_service import get_document_service
from document_store.services.validation_service import ValidationResult

from .conftest import SAMPLE_TEMPLATES, VERSIONED_TEMPLATE_OVERRIDES

ADDR = "ADDR_CASE493"


def _addr_schema(version: int, *, with_zipcode: bool) -> dict:
    fields = [{"name": "street", "label": "Street", "type": "string", "mandatory": True}]
    if with_zipcode:
        fields.append({"name": "zipcode", "label": "Zip", "type": "string", "mandatory": True})
    return {
        "template_id": ADDR, "value": ADDR, "version": version, "status": "active",
        "namespace": "wip", "identity_fields": [], "fields": fields,
    }


@pytest.fixture(autouse=True)
def _addr_templates():
    # v1: street only. v2: adds mandatory zipcode (incompatible with v1 data).
    SAMPLE_TEMPLATES[ADDR] = _addr_schema(1, with_zipcode=False)
    VERSIONED_TEMPLATE_OVERRIDES[(ADDR, 1)] = _addr_schema(1, with_zipcode=False)
    VERSIONED_TEMPLATE_OVERRIDES[(ADDR, 2)] = _addr_schema(2, with_zipcode=True)
    yield
    SAMPLE_TEMPLATES.pop(ADDR, None)
    VERSIONED_TEMPLATE_OVERRIDES.clear()


def _addr_field(version: int) -> dict:
    return {
        "name": "addr", "label": "Address", "type": "object",
        "template_ref": ADDR, "template_ref_version": version,
    }


@pytest.mark.asyncio
async def test_nested_ref_validates_against_pinned_version_not_latest(client, auth_headers):
    """Data valid for the pinned v1 but missing v2's mandatory `zipcode`
    validates clean — the nested ref resolves the pinned v1, never the
    incompatible latest v2. This is the stranding-prevention guarantee."""
    vs = get_document_service().validation_service
    result = ValidationResult()
    await vs._validate_object(
        value={"street": "1 Main"},   # valid under v1; would fail v2 (no zipcode)
        field=_addr_field(version=1),
        field_path="addr",
        template={},
        result=result,
        validation={},
    )
    assert result.valid, result.errors


@pytest.mark.asyncio
async def test_nested_ref_pin_to_v2_enforces_v2(client, auth_headers):
    """Control: pinning the same field to v2 enforces v2's mandatory zipcode,
    proving the version argument actually selects the nested schema."""
    vs = get_document_service().validation_service
    result = ValidationResult()
    await vs._validate_object(
        value={"street": "1 Main"},   # missing zipcode → invalid under v2
        field=_addr_field(version=2),
        field_path="addr",
        template={},
        result=result,
        validation={},
    )
    assert not result.valid
    blob = " ".join(str(e) for e in result.errors)
    assert "zipcode" in blob
