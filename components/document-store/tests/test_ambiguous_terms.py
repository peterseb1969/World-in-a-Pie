"""Ambiguous term names in document validation.

A name borne by several terms resolves to the first bearer in list order —
acceptable with a warning on an ordinary field, never acceptable on an
identity field, where the arbitrary pick would feed the identity hash
(the same fail-closed rule as an unresolvable identity referent).

Covers the validation policy: error `ambiguous_term` on identity fields,
warning otherwise. Which term bears which name is decided by def-store
alone; how its answer becomes `ambiguous` + `candidates` is tested in
wip-auth's test_term_resolution.py.
"""

from unittest.mock import AsyncMock, patch

import pytest

from document_store.services.validation_service import (
    ValidationResult,
    ValidationService,
)

# ============================================================================
# Validation policy: identity fields fail closed, others warn
# ============================================================================

TEMPLATE = {
    "identity_fields": ["molecule"],
    "fields": [
        {"name": "molecule", "type": "term", "terminology_ref": "MOLECULES"},
        {"name": "comparator", "type": "term", "terminology_ref": "MOLECULES"},
    ],
}

AMBIGUOUS_RESULT = {
    "valid": True,
    "matched_term": {"term_id": "T-1", "value": "RO4876646"},
    "matched_via": "alias",
    "ambiguous": True,
    "candidates": [
        {"term_id": "T-1", "value": "RO4876646"},
        {"term_id": "T-2", "value": "RO4876646-000"},
    ],
}


async def _run_validation(data: dict) -> ValidationResult:
    service = ValidationService()
    result = ValidationResult()
    mock_client = AsyncMock()
    mock_client.validate_values_bulk = AsyncMock(
        return_value=[AMBIGUOUS_RESULT] * len(data)
    )
    with patch(
        "document_store.services.validation_service.get_def_store_client",
        return_value=mock_client,
    ):
        await service._validate_terms(data, TEMPLATE, result, namespace="wip")
    return result


@pytest.mark.asyncio
async def test_ambiguous_identity_field_fails_closed():
    result = await _run_validation({"molecule": "bevacizumab"})
    assert result.valid is False
    error = result.errors[0]
    assert error["code"] == "ambiguous_term"
    assert error["field"] == "molecule"
    assert len(error["details"]["candidates"]) == 2
    # The rejected field contributes NO term reference
    assert result.term_references == []


@pytest.mark.asyncio
async def test_ambiguous_plain_field_warns_and_resolves():
    result = await _run_validation({"comparator": "bevacizumab"})
    assert result.valid is True
    assert any("ambiguous" in w for w in result.warnings)
    # The first bearer still resolves — visible, not silent
    assert result.term_references[0]["term_id"] == "T-1"
