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


# =============================================================================
# CASE-303: ?include=peers on /relationships
# =============================================================================


@pytest.mark.asyncio
async def test_relationships_default_response_unchanged(
    client: AsyncClient, auth_headers: dict,
):
    """Without ?include, response shape matches pre-CASE-303 behaviour:
    no peer / peer_error_code / peer_error fields populated.
    """
    exp_id, mol_id = await _seed_endpoints(client, auth_headers)
    await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": exp_id, "target_ref": mol_id, "role": "input",
    })

    resp = await client.get(
        f"{API}/documents/{exp_id}/relationships",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item.get("peer") is None
    assert item.get("peer_error_code") is None
    assert item.get("peer_error") is None


@pytest.mark.asyncio
async def test_relationships_include_peers_embeds_peer(
    client: AsyncClient, auth_headers: dict,
):
    """?include=peers populates the peer projection on each item — direction-
    agnostic (peer is the OTHER end of the edge) and compact (title/doc_status).
    """
    exp_id, mol_id = await _seed_endpoints(client, auth_headers)
    await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": exp_id, "target_ref": mol_id, "role": "input",
    })

    resp = await client.get(
        f"{API}/documents/{exp_id}/relationships?include=peers",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    peer = items[0]["peer"]
    assert peer is not None
    # The seed is the experiment; peer should be the molecule (target_ref end).
    assert peer["document_id"] == mol_id
    assert peer["template_value"] == "MOLECULE"
    assert peer["status"] == "active"
    assert items[0].get("peer_error_code") is None


@pytest.mark.asyncio
async def test_relationships_include_peers_orphaned_ref(
    client: AsyncClient, auth_headers: dict,
):
    """When the relationship doc references a peer that no longer exists,
    peer_error_code='not_found' surfaces on that item without breaking the
    whole response.
    """
    from document_store.models.document import Document, DocumentStatus
    exp_id, mol_id = await _seed_endpoints(client, auth_headers)
    edge = await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": exp_id, "target_ref": mol_id, "role": "input",
    })

    # Hard-delete the peer doc out from under the edge to simulate an orphan.
    # Using the model directly because the API normally soft-deletes (which
    # would still resolve as inactive, not not_found).
    await Document.find_one(
        {"document_id": mol_id, "status": DocumentStatus.ACTIVE.value},
    ).delete()

    resp = await client.get(
        f"{API}/documents/{exp_id}/relationships?include=peers",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["document_id"] == edge["document_id"]
    assert item.get("peer") is None
    assert item.get("peer_error_code") == "not_found"
    assert mol_id in (item.get("peer_error") or "")


@pytest.mark.asyncio
async def test_relationships_include_peers_inactive_peer_returned(
    client: AsyncClient, auth_headers: dict,
):
    """Inactive peers per PoNIF #1 still resolve. They are returned as a
    populated peer object with status='inactive' — NOT as peer_error_code.
    The UI can render them dimmed.
    """
    from document_store.models.document import Document, DocumentStatus
    exp_id, mol_id = await _seed_endpoints(client, auth_headers)
    await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": exp_id, "target_ref": mol_id, "role": "input",
    })

    # Mark peer inactive directly (mirrors archive without going through API).
    peer_doc = await Document.find_one(
        {"document_id": mol_id, "status": DocumentStatus.ACTIVE.value},
    )
    peer_doc.status = DocumentStatus.INACTIVE
    await peer_doc.save()

    resp = await client.get(
        f"{API}/documents/{exp_id}/relationships?active_only=false&include=peers",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["peer"] is not None
    assert item["peer"]["status"] == "inactive"
    assert item.get("peer_error_code") is None  # Inactive ≠ error per PoNIF #1


@pytest.mark.asyncio
async def test_relationships_include_unknown_token_ignored(
    client: AsyncClient, auth_headers: dict,
):
    """Unknown tokens in ?include don't error out — tokens we don't recognise
    are silently ignored. Forward-compat with future include= values.
    """
    exp_id, mol_id = await _seed_endpoints(client, auth_headers)
    await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": exp_id, "target_ref": mol_id, "role": "input",
    })

    resp = await client.get(
        f"{API}/documents/{exp_id}/relationships?include=peers,whatever_future_token",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    # peers still works
    assert resp.json()["items"][0]["peer"] is not None


# Helper-level unit tests for the peer projection logic.

def test_peer_projection_serialises_with_compact_data():
    """PeerProjection accepts compact data with optional title/doc_status."""
    from document_store.models.api_models import PeerProjection
    from document_store.models.document import DocumentStatus

    proj = PeerProjection(
        document_id="abc",
        namespace="kb",
        template_id="tpl-1",
        template_value="CASE_RECORD",
        status=DocumentStatus.ACTIVE,
        data={"title": "Hello"},
    )
    dumped = proj.model_dump()
    assert dumped["document_id"] == "abc"
    assert dumped["data"] == {"title": "Hello"}
    assert dumped["status"] == DocumentStatus.ACTIVE


def test_peer_projection_serialises_with_metadata():
    """PeerProjection optional metadata field round-trips (CASE-343)."""
    from document_store.models.api_models import PeerProjection
    from document_store.models.document import DocumentStatus

    proj = PeerProjection(
        document_id="abc",
        namespace="kb",
        template_id="tpl-1",
        template_value="CASE_RECORD",
        status=DocumentStatus.ACTIVE,
        data={"case_number": 343},
        metadata={"custom": {"case_status": "open"}},
    )
    dumped = proj.model_dump()
    assert dumped["data"] == {"case_number": 343}
    assert dumped["metadata"] == {"custom": {"case_status": "open"}}


# ============================================================================
# CASE-343 — template-aware header_fields projection
# ============================================================================


@pytest.mark.asyncio
async def test_relationships_include_peers_uses_header_fields(
    client: AsyncClient, auth_headers: dict,
):
    """When the peer template declares header_fields, the projection
    includes exactly those fields — both data.* and metadata.custom.*
    paths flow through. CASE-343.
    """
    # Create two CASE_RECORD docs with case_number identity + metadata.custom.case_status.
    # API convention: the top-level `metadata` field IS the custom dict
    # (document_service wraps it as DocumentMetadata(custom=metadata)). So a
    # CASE-343 `metadata.custom.case_status` path resolves against the flat
    # dict passed here.
    case1 = await _create_doc(client, auth_headers, "CASE_RECORD", {
        "case_number": 343, "title": "Header fields", "doc_status": "active",
    }, metadata={"case_status": "open", "noise_field": "should_be_skipped"})
    case2 = await _create_doc(client, auth_headers, "CASE_RECORD", {
        "case_number": 344, "title": "Replace mode fix", "doc_status": "active",
    }, metadata={"case_status": "implemented"})

    # Edge between them
    await _create_doc(client, auth_headers, "REFERENCES", {
        "source_ref": case1["document_id"], "target_ref": case2["document_id"],
    })

    resp = await client.get(
        f"{API}/documents/{case1['document_id']}/relationships?include=peers",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    peer = items[0]["peer"]
    assert peer is not None
    assert peer["document_id"] == case2["document_id"]
    # header_fields = ["case_number", "metadata.custom.case_status"]:
    assert peer["data"] == {"case_number": 344}
    assert peer["metadata"] == {"custom": {"case_status": "implemented"}}
    # Other fields (title, doc_status, metadata.custom.noise_field) absent
    assert "title" not in peer["data"]
    assert "doc_status" not in peer["data"]


@pytest.mark.asyncio
async def test_relationships_include_peers_falls_back_to_identity_fields(
    client: AsyncClient, auth_headers: dict,
):
    """When the peer template has no header_fields, the projection falls
    back to identity_fields. MOLECULE has identity_fields=['molecule_id']
    and no header_fields declaration → peer.data contains molecule_id.
    CASE-343 default-to-identity_fields path.
    """
    exp_id, mol_id = await _seed_endpoints(client, auth_headers)
    await _create_doc(client, auth_headers, "EXPERIMENT_INPUT", {
        "source_ref": exp_id, "target_ref": mol_id, "role": "input",
    })

    resp = await client.get(
        f"{API}/documents/{exp_id}/relationships?include=peers",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    peer = items[0]["peer"]
    assert peer is not None
    # MOLECULE's identity_fields=['molecule_id']; fallback projects it.
    # MOLECULE declares neither `title` nor `doc_status` as fields, so
    # the CASE-354 auto-include doesn't fire — peer.data stays
    # identity-only (no regression for opt-out templates).
    assert peer["data"] == {"molecule_id": "MOL-001"}
    # metadata stays None when no metadata.custom.* path is in the projection set
    assert peer.get("metadata") is None


# ============================================================================
# CASE-354 — tier-2 auto-include title + doc_status when declared
# ============================================================================


@pytest.mark.asyncio
async def test_relationships_include_peers_tier2_auto_includes_title_and_doc_status(
    client: AsyncClient, auth_headers: dict,
):
    """CASE-354: tier 2 auto-includes title + doc_status when the
    template declares them, alongside identity_fields.

    LESSON has identity_fields=['lesson_id'], no header_fields, and
    declares both `title` and `doc_status` as fields. The peer
    projection should carry all three — closes the regression that
    UIs hit when identity != title and showed UUIDs in relationship
    sidebars.
    """
    lesson1 = await _create_doc(client, auth_headers, "LESSON", {
        "lesson_id": "L-001",
        "title": "Strict identity beats clever identity",
        "doc_status": "active",
        "body": "irrelevant",
    })
    lesson2 = await _create_doc(client, auth_headers, "LESSON", {
        "lesson_id": "L-002",
        "title": "Validate before you assert",
        "doc_status": "active",
    })

    await _create_doc(client, auth_headers, "MENTIONS", {
        "source_ref": lesson1["document_id"],
        "target_ref": lesson2["document_id"],
    })

    resp = await client.get(
        f"{API}/documents/{lesson1['document_id']}/relationships?include=peers",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    peer = resp.json()["items"][0]["peer"]
    assert peer is not None
    assert peer["document_id"] == lesson2["document_id"]
    # All three fields land in peer.data — identity + auto-included
    # title + auto-included doc_status.
    assert peer["data"] == {
        "lesson_id": "L-002",
        "title": "Validate before you assert",
        "doc_status": "active",
    }
    # `body` is declared on the template but isn't part of identity or
    # the auto-include set — must stay out.
    assert "body" not in peer["data"]


@pytest.mark.asyncio
async def test_relationships_include_peers_tier2_only_title_when_doc_status_undeclared(
    client: AsyncClient, auth_headers: dict,
):
    """CASE-354: the 'if field declared' guard fires per-field.

    LESSON_NO_STATUS declares `title` but NOT `doc_status`. Tier 2
    should auto-include title but NOT project a non-existent
    doc_status key.
    """
    lite1 = await _create_doc(client, auth_headers, "LESSON_NO_STATUS", {
        "lesson_id": "LITE-001", "title": "Lite first",
    })
    lite2 = await _create_doc(client, auth_headers, "LESSON_NO_STATUS", {
        "lesson_id": "LITE-002", "title": "Lite second",
    })

    await _create_doc(client, auth_headers, "MENTIONS", {
        "source_ref": lite1["document_id"],
        "target_ref": lite2["document_id"],
    })

    resp = await client.get(
        f"{API}/documents/{lite1['document_id']}/relationships?include=peers",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    peer = resp.json()["items"][0]["peer"]
    assert peer is not None
    # Identity + auto-included title; `doc_status` was not declared on
    # the template so it stays absent.
    assert peer["data"] == {"lesson_id": "LITE-002", "title": "Lite second"}
    assert "doc_status" not in peer["data"]
