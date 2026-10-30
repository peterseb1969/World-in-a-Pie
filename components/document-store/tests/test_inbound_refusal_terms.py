"""Term values from a terminology in a closed namespace the writer cannot read.

The decision is made per terminology, not per term: every value is invalid,
without a suggestion, whether the term exists or not. So a present term and
an absent one produce the same error, and no suggestion reveals a value of
the closed vocabulary. Ambiguity candidates are never reached either.
"""

from unittest.mock import AsyncMock, patch

import pytest

from document_store.services.inbound_gate import InboundGate, reset_gate, use_gate
from document_store.services.validation_service import (
    ValidationResult,
    ValidationService,
)

TEMPLATE = {
    "identity_fields": [],
    "fields": [{"name": "drug", "type": "term", "terminology_ref": "xns-closed:DRUGS"}],
}

PRESENT = {
    "valid": True, "terminology_id": "TERMINOLOGY-CLOSED",
    "matched_term": {"term_id": "T-1", "value": "aspirin"}, "matched_via": "value",
}
PRESENT_AMBIGUOUS = {
    **PRESENT, "ambiguous": True,
    "candidates": [{"term_id": "T-1", "value": "a"}, {"term_id": "T-2", "value": "b"}],
}
ABSENT = {"valid": False, "terminology_id": "TERMINOLOGY-CLOSED", "suggestion": "aspirin"}


class _Gate(InboundGate):
    """The closed terminology's namespace refuses this writer."""

    def __init__(self, enforce: bool = True, carried: frozenset[str] = frozenset()) -> None:
        super().__init__("wip", enforce=enforce, carried=carried)

    async def namespace_of(self, entity_id: str) -> str | None:
        return "xns-closed" if entity_id == "TERMINOLOGY-CLOSED" else None

    async def refuses(self, target_namespace, entity_id):  # type: ignore[no-untyped-def]
        if target_namespace != "xns-closed" or (entity_id and entity_id in self.carried):
            return False
        self.refused.add(entity_id or f"namespace:{target_namespace}")
        return self.enforce


async def _validate(def_store_result: dict, gate: InboundGate | None) -> ValidationResult:
    service = ValidationService()
    result = ValidationResult()
    client = AsyncMock()
    client.validate_values_bulk = AsyncMock(return_value=[def_store_result])
    token = use_gate(gate)
    try:
        with patch(
            "document_store.services.validation_service.get_def_store_client",
            return_value=client,
        ):
            await service._validate_terms({"drug": "aspirin"}, TEMPLATE, result, namespace="wip")
    finally:
        reset_gate(token)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("present", [PRESENT, PRESENT_AMBIGUOUS])
async def test_a_present_term_reads_as_an_absent_one(present: dict) -> None:
    refused = await _validate(present, _Gate())
    absent = await _validate(ABSENT, _Gate())
    assert refused.errors == absent.errors
    assert refused.errors[0]["code"] == "invalid_term"
    assert refused.errors[0]["details"]["suggestion"] is None
    assert refused.term_references == [] and refused.warnings == []


@pytest.mark.asyncio
async def test_without_the_gate_the_absent_value_keeps_its_suggestion() -> None:
    result = await _validate(ABSENT, None)
    assert result.errors[0]["details"]["suggestion"] == "aspirin"


@pytest.mark.asyncio
async def test_a_carried_term_resolves() -> None:
    result = await _validate(PRESENT, _Gate(carried=frozenset({"T-1"})))
    assert result.valid and result.term_references[0]["term_id"] == "T-1"


@pytest.mark.asyncio
async def test_a_recording_gate_only_notes_the_refusal() -> None:
    gate = _Gate(enforce=False)
    result = await _validate(PRESENT, gate)
    assert result.valid and gate.refused == {"T-1"}
