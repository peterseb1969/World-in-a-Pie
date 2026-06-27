"""CASE-515 — document-store side of edge-type endpoint widening (fast follow-up).

The widen op itself (`add_edge_type_endpoints`) is template-store-tested. This
proves the cross-service behaviour it unlocks: document-store endpoint
enforcement reads the edge type's `target_templates`, so an edge to a newly
allowed endpoint that is rejected BEFORE the widen is accepted AFTER it.

The document-store unit harness mocks template-store via `SAMPLE_TEMPLATES`
(one dict object shared across the UUID / legacy / value keys), so the widen is
simulated by mutating that mock — the same shape `add_edge_type_endpoints`
persists (append to the template-level `target_templates` AND the mirrored
`target_ref` field `target_templates`). The autouse fixture rebuilds
`SAMPLE_TEMPLATES` per test, so the mutation is test-local.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from .conftest import SAMPLE_TEMPLATES
from .test_relationship_documents import _create_doc, _seed_endpoints


def _widen_experiment_input_target(new_value: str) -> None:
    """Simulate add_edge_type_endpoints(EXPERIMENT_INPUT, add_target=[new_value])."""
    tpl = SAMPLE_TEMPLATES["EXPERIMENT_INPUT"]
    if new_value not in tpl["target_templates"]:
        tpl["target_templates"] = [*tpl["target_templates"], new_value]
    for f in tpl["fields"]:
        if f["name"] == "target_ref" and new_value not in (f.get("target_templates") or []):
            f["target_templates"] = [*(f.get("target_templates") or []), new_value]


@pytest.mark.asyncio
async def test_edge_to_new_endpoint_rejected_then_accepted_after_widen(
    client: AsyncClient, auth_headers: dict,
):
    exp_id, _mol_id = await _seed_endpoints(client, auth_headers)
    person = await _create_doc(client, auth_headers, "PERSON", {
        "national_id": "515000001", "first_name": "Edge", "last_name": "Case",
    })
    assert person["status"] == "created", person
    person_id = person["document_id"]

    # PERSON is NOT yet an allowed target_ref endpoint of EXPERIMENT_INPUT → rejected.
    rejected = await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": exp_id, "target_ref": person_id, "role": "input",
    })
    assert rejected["status"] == "error", rejected
    err = rejected.get("error") or ""
    assert "target_ref" in err or "invalid_reference_template" in err or "PERSON" in err

    # Widen the edge type to allow PERSON as a target (what add_edge_type_endpoints does).
    _widen_experiment_input_target("PERSON")

    # The same edge now validates and writes end-to-end.
    accepted = await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": exp_id, "target_ref": person_id, "role": "input",
    })
    assert accepted["status"] == "created", accepted
    assert accepted["document_id"] is not None


@pytest.mark.asyncio
async def test_widen_does_not_loosen_an_unrelated_endpoint(
    client: AsyncClient, auth_headers: dict,
):
    """Widening target to PERSON must not also allow PERSON on the SOURCE side —
    the additive change is scoped to the endpoint it was applied to."""
    _exp_id, mol_id = await _seed_endpoints(client, auth_headers)
    person = await _create_doc(client, auth_headers, "PERSON", {
        "national_id": "515000002", "first_name": "Source", "last_name": "Wrong",
    })
    person_id = person["document_id"]

    _widen_experiment_input_target("PERSON")  # target side only

    # source_ref still requires EXPERIMENT — a PERSON source stays rejected.
    result = await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": person_id, "target_ref": mol_id, "role": "input",
    })
    assert result["status"] == "error", result
    # Still rejected because the SOURCE side requires EXPERIMENT — the target-side
    # widen did not loosen it.
    assert "EXPERIMENT" in (result.get("error") or "")
