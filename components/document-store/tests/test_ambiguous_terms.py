"""Ambiguous term names in document validation.

A name borne by several terms resolves to the first bearer in list order —
acceptable with a warning on an ordinary field, never acceptable on an
identity field, where the arbitrary pick would feed the identity hash
(the same fail-closed rule as an unresolvable identity referent).

Covers: the lookup build recording every bearer of a contested name key
(value-owned keys stay unambiguous — precedence is a rule, not a tie),
the local validator surfacing the bearers, and the validation policy:
error `ambiguous_term` on identity fields, warning otherwise.
"""

from unittest.mock import AsyncMock, patch

import pytest

from document_store.services.def_store_client import DefStoreClient
from document_store.services.validation_service import (
    ValidationResult,
    ValidationService,
)

# ============================================================================
# Lookup build and local validation
# ============================================================================

TERMS = [
    {"term_id": "T-1", "value": "RO4876646", "aliases": ["bevacizumab", "Avastin"]},
    {"term_id": "T-2", "value": "RO4876646-000", "aliases": ["bevacizumab"]},
    # T-3's value equals T-1's alias spelled differently — value precedence
    # binds the key to T-3, which is a rule, not an ambiguity
    {"term_id": "T-3", "value": "avastin", "aliases": []},
]


def _build():
    client = DefStoreClient.__new__(DefStoreClient)
    return client._build_term_lookup(TERMS)


def test_lookup_records_every_bearer_of_contested_name():
    lookup, ambiguous = _build()
    # Both bearers recorded, first in list order holds the binding
    assert lookup["bevacizumab"]["term"]["term_id"] == "T-1"
    bearer_ids = [t["term_id"] for t in ambiguous["bevacizumab"]]
    assert bearer_ids == ["T-1", "T-2"]


def test_value_owned_key_is_not_ambiguous():
    lookup, ambiguous = _build()
    # 'avastin' is T-3's canonical value; T-1's alias never contests it
    assert lookup["avastin"]["term"]["term_id"] == "T-3"
    assert lookup["avastin"]["matched_via"] == "value"
    assert "avastin" not in ambiguous


def test_local_validation_surfaces_bearers():
    client = DefStoreClient.__new__(DefStoreClient)
    lookup, ambiguous = client._build_term_lookup(TERMS)
    terminology = {"_lookup": lookup, "_ambiguous": ambiguous}

    result = client._validate_term_locally(terminology, "Bevacizumab")
    assert result["valid"] is True
    assert result["ambiguous"] is True
    assert [c["term_id"] for c in result["candidates"]] == ["T-1", "T-2"]

    clean = client._validate_term_locally(terminology, "avastin")
    assert clean["valid"] is True
    assert "ambiguous" not in clean


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
