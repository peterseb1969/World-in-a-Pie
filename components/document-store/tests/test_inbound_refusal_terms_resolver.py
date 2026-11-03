"""Term refusal through the real resolver and the real gate.

test_inbound_refusal_terms.py feeds validation hand-made resolution results.
Those once carried a terminology_id the real resolver never returned, so the
gate passed every term value in production while its tests passed. Here
values resolve through wip_auth.term_resolution against a stand-in for
def-store's /validate/bulk, and the gate is the real InboundGate; only the
Registry's answers (where the terminology lives, whether the writer may read
that namespace) are stubbed.
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from document_store.services import inbound_gate
from document_store.services import validation_service as vs
from document_store.services.def_store_client import DefStoreClient
from document_store.services.inbound_gate import (
    InboundGate,
    placement_batch,
    reset_gate,
    use_gate,
)
from document_store.services.validation_service import ValidationResult, ValidationService
from wip_auth.term_resolution import set_def_store_transport, term_resolution_batch

TERMINOLOGY = "0190c000-0000-7000-0000-0000000010aa"
CLOSED = "xns-closed"
TEMPLATE = {
    "identity_fields": [],
    "fields": [{"name": "drug", "type": "term", "terminology_ref": TERMINOLOGY}],
}


def _def_store(request: httpx.Request) -> httpx.Response:
    """'aspirin' is a term; anything else is not, with 'aspirin' suggested."""
    items = json.loads(request.content)["items"]
    return httpx.Response(200, json={"results": [
        {"valid": True, "matched_via": "value",
         "matched_term": {"term_id": "T-aspirin", "value": "aspirin"}}
        if it["value"] == "aspirin" else
        {"valid": False, "suggestion": {"term_id": "T-aspirin", "value": "aspirin"}}
        for it in items
    ]})


@pytest.fixture
def world():
    """The terminology lives in a closed namespace; lookups are counted."""
    lookups: list[str] = []

    async def lookup(entity_id: str) -> str | None:
        lookups.append(entity_id)
        return CLOSED if entity_id == TERMINOLOGY else None

    validator = AsyncMock()
    validator._get_namespace = AsyncMock(return_value={"inbound_references": "closed"})
    set_def_store_transport(httpx.MockTransport(_def_store))
    try:
        with (
            patch.object(vs, "get_def_store_client",
                         return_value=DefStoreClient(base_url="http://def-store", api_key="k")),
            patch.object(inbound_gate, "_lookup_namespace", side_effect=lookup),
            patch.object(inbound_gate, "get_reference_validator", return_value=validator),
        ):
            yield lookups
    finally:
        set_def_store_transport(None)


async def _validate(value: str, gate: InboundGate | None) -> ValidationResult:
    result = ValidationResult()
    token = use_gate(gate)
    try:
        await ValidationService()._validate_terms({"drug": value}, TEMPLATE, result, namespace="app")
    finally:
        reset_gate(token)
    return result


@pytest.mark.asyncio
async def test_a_closed_namespaces_term_is_refused_like_an_absent_one(world):
    with patch.object(inbound_gate, "caller_can_read", AsyncMock(return_value=False)):
        present = await _validate("aspirin", InboundGate("app"))
        absent = await _validate("asprin", InboundGate("app"))
    assert not present.valid and not absent.valid
    assert present.errors[0]["code"] == absent.errors[0]["code"] == "invalid_term"
    assert present.errors[0]["details"]["suggestion"] is None
    assert absent.errors[0]["details"]["suggestion"] is None
    assert present.term_references == []


@pytest.mark.asyncio
async def test_a_reader_of_the_namespace_gets_the_term(world):
    with patch.object(inbound_gate, "caller_can_read", AsyncMock(return_value=True)):
        result = await _validate("aspirin", InboundGate("app"))
    assert result.valid and result.term_references[0]["term_id"] == "T-aspirin"


@pytest.mark.asyncio
async def test_a_term_the_previous_version_held_stays_usable(world):
    with patch.object(inbound_gate, "caller_can_read", AsyncMock(return_value=False)):
        result = await _validate("aspirin", InboundGate("app", carried=frozenset({"T-aspirin"})))
    assert result.valid


@pytest.mark.asyncio
async def test_a_batch_places_the_terminology_once(world):
    with (
        patch.object(inbound_gate, "caller_can_read", AsyncMock(return_value=False)),
        term_resolution_batch(), placement_batch(),
    ):
        results = [await _validate("aspirin", InboundGate("app")) for _ in range(5)]
    assert not any(r.valid for r in results)
    assert world == [TERMINOLOGY]


@pytest.mark.asyncio
async def test_without_a_batch_each_document_places_it(world):
    with patch.object(inbound_gate, "caller_can_read", AsyncMock(return_value=False)):
        for _ in range(3):
            await _validate("aspirin", InboundGate("app"))
    assert world == [TERMINOLOGY] * 3
