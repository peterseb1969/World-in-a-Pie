"""Tests for Phase-2 relationship-document write-time validation.

Phase 1 (template-store) ensured that relationship templates have the
right shape. Phase 2 (document-store) ensures that documents *created
against* a relationship template have valid endpoints — same namespace,
not archived — on top of the standard reference-field validation that
already verifies the source/target docs exist and live in the right
template family.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

API = "/api/document-store"


async def _create_doc(
    client: AsyncClient, auth_headers: dict, template_value: str, data: dict, **extra,
) -> dict:
    """POST one document via the bulk endpoint, return its BulkResultItem."""
    resp = await client.post(
        f"{API}/documents",
        headers=auth_headers,
        json=[{
            "namespace": "wip",
            "template_id": template_value,
            "data": data,
            **extra,
        }],
    )
    assert resp.status_code == 200, f"Create failed: {resp.text}"
    return resp.json()["results"][0]


async def _seed_endpoints(client: AsyncClient, auth_headers: dict) -> tuple[str, str]:
    """Create one EXPERIMENT and one MOLECULE document; return their IDs."""
    exp = await _create_doc(client, auth_headers, "EXPERIMENT", {
        "experiment_id": "EXP-001", "name": "Test experiment",
    })
    mol = await _create_doc(client, auth_headers, "MOLECULE", {
        "molecule_id": "MOL-001", "name": "Test molecule",
    })
    assert exp["status"] == "created", exp
    assert mol["status"] == "created", mol
    return exp["document_id"], mol["document_id"]


# =============================================================================
# Happy path
# =============================================================================


@pytest.mark.asyncio
async def test_create_relationship_document_happy_path(
    client: AsyncClient, auth_headers: dict,
):
    """A relationship document with valid same-namespace endpoints succeeds."""
    exp_id, mol_id = await _seed_endpoints(client, auth_headers)

    result = await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": exp_id,
        "target_ref": mol_id,
        "role": "input",
    })
    assert result["status"] == "created", result
    assert result["document_id"] is not None


# =============================================================================
# Cross-namespace rejection
# =============================================================================


@pytest.mark.asyncio
async def test_create_relationship_rejects_cross_namespace_source(
    client: AsyncClient, auth_headers: dict,
):
    """source_ref pointing to a document in a different namespace is rejected
    with the cross_namespace_relationship error code."""
    # Seed the molecule in 'wip' (target namespace stays valid).
    _, mol_id = await _seed_endpoints(client, auth_headers)

    # Create an EXPERIMENT in a different namespace.
    other_resp = await client.post(
        f"{API}/documents",
        headers=auth_headers,
        json=[{
            "namespace": "other-ns",
            "template_id": "EXPERIMENT",
            "data": {"experiment_id": "EXP-OTHER"},
        }],
    )
    assert other_resp.status_code == 200
    other_result = other_resp.json()["results"][0]
    # Some test stacks reject 'other-ns' before the doc is created — if the
    # test fixture doesn't allow that namespace, skip rather than false-fail.
    if other_result["status"] != "created":
        pytest.skip(f"test fixture does not support namespace 'other-ns': {other_result}")
    other_exp_id = other_result["document_id"]

    # Now create the relationship document in 'wip', pointing at the
    # cross-namespace experiment.
    result = await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": other_exp_id,
        "target_ref": mol_id,
        "role": "input",
    })
    assert result["status"] == "error", result
    assert "cross_namespace_relationship" in (result.get("error") or "")
    assert "source_ref" in (result.get("error") or "")


# =============================================================================
# Wrong-template rejection (pre-existing reference-field check, still works)
# =============================================================================


@pytest.mark.asyncio
async def test_create_relationship_rejects_wrong_template_endpoint(
    client: AsyncClient, auth_headers: dict,
):
    """source_ref pointing to a PERSON (not EXPERIMENT) must be rejected.

    This exercises the pre-existing reference-field validation, not the
    new Phase-2 layer, but confirms that the layered validation still
    works for relationship templates."""
    # Create a PERSON to use as the wrong endpoint.
    person = await _create_doc(client, auth_headers, "PERSON", {
        "national_id": "999999999",
        "first_name": "Wrong",
        "last_name": "Endpoint",
    })
    assert person["status"] == "created"

    _, mol_id = await _seed_endpoints(client, auth_headers)

    result = await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": person["document_id"],
        "target_ref": mol_id,
        "role": "input",
    })
    assert result["status"] == "error", result
    # Standard error code from validation_service._verify_ref_template_and_build_result.
    assert "invalid_reference_template" in (result.get("error") or "") or "PERSON" in (result.get("error") or "")


# =============================================================================
# Non-existent endpoint rejection
# =============================================================================


@pytest.mark.asyncio
async def test_create_relationship_rejects_unresolvable_endpoint(
    client: AsyncClient, auth_headers: dict,
):
    """source_ref that doesn't resolve to any document is rejected."""
    _, mol_id = await _seed_endpoints(client, auth_headers)

    result = await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": "0190ffff-ffff-7fff-8fff-ffffffffffff",  # nonexistent UUID
        "target_ref": mol_id,
        "role": "input",
    })
    assert result["status"] == "error", result
    err = result.get("error") or ""
    assert "reference_not_found" in err or "not found" in err.lower()


# =============================================================================
# Entity templates remain unaffected by the new validator
# =============================================================================


@pytest.mark.asyncio
async def test_entity_template_create_unaffected_by_relationship_validator(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict,
):
    """The new relationship validator must not interfere with entity-template
    document creation. PERSON has no usage field (defaults to entity)."""
    result = await _create_doc(client, auth_headers, "PERSON", sample_person_data)
    assert result["status"] == "created", result
