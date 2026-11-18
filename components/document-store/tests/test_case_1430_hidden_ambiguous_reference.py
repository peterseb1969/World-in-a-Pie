"""An ambiguous reference into a namespace the writer may not reference reads
as a reference to nothing (CASE-1430).

A reference that several entities answer was reported as
`ambiguous_reference`, with the candidates, before the inbound gate was
asked. A writer who may not reference a closed namespace was therefore shown
its documents (ids, template values, matched keys), while a value nothing
bears got `reference_not_found`. The gate is now asked of every candidate;
when it refuses all of them the write gets exactly the not-found answer. A
writer who may reference the namespace keeps the ambiguity and its
candidates.

The document path runs for real: 'xns-closed' is closed (the default) and
only the reader key reads it; every MOLECULE document carries the template's
value in its auto-synonym, so two of them make `xns-closed:MOLECULE`
ambiguous. The terminology and target-template paths take the ambiguity
from def-store and the Registry, which hold none here, so it is faked.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient

from document_store.services import validation_service as vs
from document_store.services.def_store_client import DefStoreClient
from document_store.services.validation_service import ValidationResult, ValidationService
from wip_auth.registry_client import AmbiguousIdentifierError

API = "/api/document-store"
READER = {"X-API-Key": "test_xns_reader_key"}
WRITER = {"X-API-Key": "test_xns_writer_key"}


async def _post(client: AsyncClient, headers: dict, items: list[dict]) -> dict:
    resp = await client.post(f"{API}/documents", headers=headers, json=items)
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


def _edge(source: str, target: str) -> list[dict]:
    return [{
        "namespace": "wip", "template_id": "wip:EXPERIMENT_INPUT",
        "data": {"source_ref": source, "target_ref": target, "role": "input"},
    }]


@pytest.fixture
async def two_molecules(client: AsyncClient, auth_headers: dict) -> tuple[str, list[str]]:
    exp = await _post(client, auth_headers, [{
        "namespace": "wip", "template_id": "wip:EXPERIMENT",
        "data": {"experiment_id": "EXP-1430", "name": "x"},
    }])
    molecules = [
        await _post(client, auth_headers, [{
            "namespace": "xns-closed", "template_id": "wip:MOLECULE",
            "data": {"molecule_id": f"MOL-1430-{n}", "name": "x"},
        }])
        for n in (1, 2)
    ]
    assert exp["status"] == "created" and all(m["status"] == "created" for m in molecules)
    return exp["document_id"], [m["document_id"] for m in molecules]


@pytest.mark.asyncio
async def test_a_hidden_ambiguous_document_reference_reads_as_missing(
    client: AsyncClient, two_molecules,
) -> None:
    exp_id, molecule_ids = two_molecules
    hidden = await _post(client, WRITER, _edge(exp_id, "xns-closed:MOLECULE"))
    unknown = await _post(client, WRITER, _edge(exp_id, "xns-closed:NO_SUCH_VALUE"))
    assert hidden["status"] == "error", hidden
    body = json.dumps(hidden)
    assert not any(m in body for m in molecule_ids), body
    assert body.replace("xns-closed:MOLECULE", "<V>") == json.dumps(unknown).replace(
        "xns-closed:NO_SUCH_VALUE", "<V>",
    )


@pytest.mark.asyncio
async def test_a_writer_who_may_reference_the_namespace_sees_the_ambiguity(
    client: AsyncClient, two_molecules,
) -> None:
    """The control: the same value is ambiguous for a writer who reads the
    namespace, so the hidden case above reached the ambiguity."""
    exp_id, molecule_ids = two_molecules
    item = await _post(client, READER, _edge(exp_id, "xns-closed:MOLECULE"))
    [error] = [e for e in item["details"]["errors"] if e["code"] == "ambiguous_reference"]
    assert {c["document_id"] for c in error["details"]["candidates"]} == set(molecule_ids)


# ── terminology references: def-store's 409, asked with the service key ──

CANDIDATES = [
    {"entry_id": "T-HIDDEN-1", "namespace": "hidden"},
    {"entry_id": "T-HIDDEN-2", "namespace": "hidden"},
    {"entry_id": "T-SEEN", "namespace": "seen"},
]


def _def_store(candidates: list[dict]):  # type: ignore[no-untyped-def]
    def answer(request: httpx.Request) -> httpx.Response:
        if "NOPE" in request.url.path:
            return httpx.Response(404, json={"detail": "Terminology not found"})
        message = "terminology identifier is ambiguous: " + ", ".join(c["entry_id"] for c in candidates)
        return httpx.Response(409, json={"detail": {
            "error": "ambiguous", "error_code": "ambiguous_reference",
            "message": message, "candidates": candidates,
        }})
    return answer


async def _hidden_refused(namespace: str | None, entity_id: str | None) -> bool:
    return namespace == "hidden"


async def _resolve_terminology(value: str, candidates: list[dict]) -> ValidationResult:
    client = DefStoreClient(base_url="http://def-store", api_key="k")
    result = ValidationResult()
    with patch("document_store.services.def_store_client.get_def_store_transport",
               return_value=httpx.MockTransport(_def_store(candidates))), \
         patch.object(vs, "get_def_store_client", return_value=client), \
         patch.object(vs, "gate_refuses", side_effect=_hidden_refused):
        await ValidationService()._resolve_terminology_reference(value, result, "vocab", namespace="appns")
    return result


@pytest.mark.asyncio
async def test_a_terminology_ambiguous_only_among_hidden_ones_reads_as_missing() -> None:
    hidden = await _resolve_terminology("hidden:SHADE", CANDIDATES[:2])
    unknown = await _resolve_terminology("hidden:NOPE", CANDIDATES[:2])
    assert [e["code"] for e in hidden.errors] == ["invalid_terminology_reference"]
    assert json.dumps(hidden.errors).replace("SHADE", "<V>") == json.dumps(unknown.errors).replace("NOPE", "<V>")
    assert "T-HIDDEN" not in json.dumps(hidden.errors)


@pytest.mark.asyncio
async def test_a_terminology_ambiguity_names_only_the_candidates_the_writer_may_see() -> None:
    result = await _resolve_terminology("SHADE", CANDIDATES)
    [error] = result.errors
    assert error["code"] == "ambiguous_reference"
    assert "T-SEEN" in error["message"] and "T-HIDDEN" not in error["message"]


@pytest.mark.asyncio
async def test_a_terminology_ambiguity_among_visible_ones_keeps_def_stores_answer() -> None:
    result = await _resolve_terminology("SHADE", [*CANDIDATES[2:], {"entry_id": "T-SEEN-2", "namespace": "seen"}])
    [error] = result.errors
    assert error["code"] == "ambiguous_reference"
    assert "T-SEEN" in error["message"] and "T-SEEN-2" in error["message"]


# ── target templates: the Registry's ambiguity ──


async def _allowed(candidates: list[dict]) -> set[str]:
    registry = AsyncMock()
    registry.resolve_identifier = AsyncMock(
        side_effect=AmbiguousIdentifierError("hidden:TWIN", candidates, len(candidates)),
    )
    with patch("document_store.services.registry_client.get_registry_client", return_value=registry), \
         patch.object(vs, "gate_refuses", side_effect=_hidden_refused):
        return await ValidationService()._allowed_template_ids(["hidden:TWIN"], "appns")


@pytest.mark.asyncio
async def test_a_target_template_ambiguous_only_among_hidden_ones_does_not_resolve() -> None:
    assert await _allowed(CANDIDATES[:2]) == set()


@pytest.mark.asyncio
async def test_a_target_template_ambiguity_names_only_visible_candidates() -> None:
    with pytest.raises(AmbiguousIdentifierError) as raised:
        await _allowed(CANDIDATES)
    assert [c["entry_id"] for c in raised.value.candidates] == ["T-SEEN"]
