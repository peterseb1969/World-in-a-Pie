"""Tests for the Phase-6 NATS event payload enrichment.

When a relationship-template document fires a NATS event, the payload
must include enough state for an external subscriber (Snowflake,
BigQuery, an audit pipeline, etc.) to rebuild the edge without
querying back. Specifically:

  - top-level `template_usage` mirrors the template's usage flag
  - `data.source_ref_resolved` / `data.target_ref_resolved` carry the
    canonical document_id of each endpoint
  - `data.source_template_value` / `data.target_template_value` carry
    the endpoint template's value code

For non-relationship templates the enrichment is a no-op.

These tests construct the payload directly via
DocumentService._document_to_event_payload (the same method called
from every publish_document_event site), so the enrichment is
exercised regardless of NATS being mocked or live.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from document_store.models.document import Document
from document_store.services.document_service import get_document_service

API = "/api/document-store"


async def _create_doc(client, auth_headers, template_value: str, data: dict) -> dict:
    resp = await client.post(
        f"{API}/documents",
        headers=auth_headers,
        json=[{"namespace": "wip", "template_id": template_value, "data": data}],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


# =============================================================================
# Relationship enrichment
# =============================================================================


@pytest.mark.asyncio
async def test_relationship_event_payload_includes_resolved_endpoints(
    client: AsyncClient, auth_headers: dict,
):
    """A relationship document's event payload must carry the resolved
    endpoint document_ids and template values inline under data.*."""
    exp = await _create_doc(client, auth_headers, "EXPERIMENT", {
        "experiment_id": "EXP-EVT", "name": "evt test",
    })
    mol = await _create_doc(client, auth_headers, "MOLECULE", {
        "molecule_id": "MOL-EVT",
    })
    rel = await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": exp["document_id"],
        "target_ref": mol["document_id"],
        "role": "input",
    })
    assert rel["status"] == "created", rel

    rel_doc = await Document.find_one({"document_id": rel["document_id"]})
    assert rel_doc is not None

    payload = await get_document_service()._document_to_event_payload(rel_doc)

    # Top-level marker so subscribers can route relationship events.
    assert payload.get("template_usage") == "relationship", payload

    # Resolved endpoint info inside data.
    data = payload["data"]
    assert data["source_ref_resolved"] == exp["document_id"], data
    assert data["target_ref_resolved"] == mol["document_id"], data
    assert data["source_template_value"] == "EXPERIMENT", data
    assert data["target_template_value"] == "MOLECULE", data
    # Original lookup values are preserved.
    assert data["source_ref"] == exp["document_id"]
    assert data["target_ref"] == mol["document_id"]


# =============================================================================
# Non-relationship templates: no enrichment
# =============================================================================


@pytest.mark.asyncio
async def test_entity_event_payload_has_no_relationship_keys(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict,
):
    """PERSON (entity template) event payload must NOT include the
    Phase-6 relationship-only keys."""
    person = await _create_doc(client, auth_headers, "PERSON", sample_person_data)
    person_doc = await Document.find_one({"document_id": person["document_id"]})
    assert person_doc is not None

    payload = await get_document_service()._document_to_event_payload(person_doc)

    assert "template_usage" not in payload, payload
    data = payload["data"]
    for forbidden in (
        "source_ref_resolved", "target_ref_resolved",
        "source_template_value", "target_template_value",
    ):
        assert forbidden not in data, f"{forbidden} should not appear on entity templates"
