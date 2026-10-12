"""Resolved-referent identity (CASE-847 / DECISION-2).

A term- or reference-typed identity field feeds its resolved referent's
canonical ID into the identity hash, never the submitted spelling — so
every alias of one term converges on one document. The rule is
fail-closed: an identity field whose resolution is missing (for example a
Def-Store outage, which degrades non-identity term fields to a warning)
rejects the write instead of hashing the spelling.

The conftest Def-Store mock resolves term values case-insensitively to a
stable per-term id: "M" and "m" are aliases of one term, "M" and "F" are
two distinct terms.
"""

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from document_store.services.def_store_client import DefStoreError
from document_store.services.validation_service import (
    ValidationResult,
    ValidationService,
)


async def create_one(client: AsyncClient, auth_headers: dict, template_id: str, data: dict, **extra):
    """Create a single document via the bulk-first API and return the result item."""
    payload = {"namespace": "wip", "template_id": template_id, "data": data, **extra}
    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[payload],
    )
    assert response.status_code == 200, f"Create failed: {response.text}"
    return response.json()["results"][0]


# ============================================================================
# Convergence: aliases of one term are one identity
# ============================================================================

@pytest.mark.asyncio
async def test_alias_spellings_converge_on_one_document(client: AsyncClient, auth_headers: dict):
    """Two spellings of the same term create one document, not a fork."""
    first = await create_one(
        client, auth_headers, "TERM_IDENTITY",
        {"category": "M", "note": "canonical spelling"},
    )
    assert first["status"] == "created"
    assert first["version"] == 1

    second = await create_one(
        client, auth_headers, "TERM_IDENTITY",
        {"category": "m", "note": "alias spelling"},
    )
    assert second["document_id"] == first["document_id"], (
        "an alias of the same term forked a second document"
    )
    assert second["version"] == 2
    assert second["identity_hash"] == first["identity_hash"]


@pytest.mark.asyncio
async def test_distinct_terms_stay_distinct(client: AsyncClient, auth_headers: dict):
    """Two different terms (not aliases) still create two documents."""
    first = await create_one(
        client, auth_headers, "TERM_IDENTITY", {"category": "M"},
    )
    second = await create_one(
        client, auth_headers, "TERM_IDENTITY", {"category": "F"},
    )
    assert second["document_id"] != first["document_id"]
    assert second["identity_hash"] != first["identity_hash"]


@pytest.mark.asyncio
async def test_hash_feed_is_the_term_id_not_the_spelling(client: AsyncClient, auth_headers: dict):
    """The stored document keeps the submitted spelling; only the hash feed resolves."""
    result = await create_one(
        client, auth_headers, "TERM_IDENTITY",
        {"category": "O", "note": "original value preserved"},
    )
    response = await client.get(
        f"/api/document-store/documents/{result['document_id']}",
        headers=auth_headers,
    )
    assert response.status_code == 200
    doc = response.json()
    # Preserve Original Values: data carries the spelling…
    assert doc["data"]["category"] == "O"
    # …and the resolution rides alongside for the feed.
    term_ref = next(
        tr for tr in doc["term_references"] if tr["field_path"] == "category"
    )
    assert term_ref["term_id"]


# ============================================================================
# Fail-closed: no resolution, no identity, no write
# ============================================================================

@pytest.mark.asyncio
async def test_def_store_outage_fails_closed_for_identity_term_field(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    """A resolution outage on an identity term field rejects the write."""
    from document_store.services import validation_service as vs

    broken = AsyncMock()
    broken.validate_values_bulk = AsyncMock(
        side_effect=DefStoreError("def-store unreachable")
    )
    monkeypatch.setattr(vs, "get_def_store_client", lambda: broken)

    payload = {
        "namespace": "wip",
        "template_id": "TERM_IDENTITY",
        "data": {"category": "M"},
    }
    response = await client.post(
        "/api/document-store/documents", headers=auth_headers, json=[payload],
    )
    assert response.status_code == 200  # bulk-first: per-item status inside
    item = response.json()["results"][0]
    assert item["status"] == "error"
    # Create-path validation failures surface as a formatted message (the
    # machine-readable code lives on the validation error itself; see the
    # unit test below for the identity_resolution_failed code).
    assert "did not resolve" in (item.get("error") or ""), (
        f"expected the fail-closed resolution error, got: {item}"
    )


@pytest.mark.asyncio
async def test_def_store_outage_still_warns_for_non_identity_term_field(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict, monkeypatch,
):
    """Non-identity term fields keep degrade-to-warning on outage."""
    from document_store.services import validation_service as vs

    broken = AsyncMock()
    broken.validate_values_bulk = AsyncMock(
        side_effect=DefStoreError("def-store unreachable")
    )
    monkeypatch.setattr(vs, "get_def_store_client", lambda: broken)

    data = dict(sample_person_data)
    data["gender"] = "M"  # term field, but PERSON's identity is national_id
    result = await create_one(client, auth_headers, "PERSON", data)
    assert result["status"] in ("created", "updated")
    assert any("Could not validate" in w for w in result.get("warnings") or [])


# ============================================================================
# Unit level: the stage-7 feed substitution
# ============================================================================

TERM_TEMPLATE = {
    "identity_fields": ["category"],
    "fields": [
        {"name": "category", "type": "term", "terminology_ref": "GENDER"},
        {"name": "note", "type": "string"},
    ],
}


def test_compute_identity_feeds_resolved_term_id():
    service = ValidationService()
    result = ValidationResult()
    result.term_references.append(
        {"field_path": "category", "term_id": "T-123", "terminology_ref": "GENDER"}
    )
    service._compute_identity({"category": "Alias Spelling"}, TERM_TEMPLATE, result)
    assert result.valid
    assert result.identity_values == {"category": "T-123"}
    assert result.submitted_identity_values == {"category": "Alias Spelling"}


def test_compute_identity_fails_closed_without_resolution():
    service = ValidationService()
    result = ValidationResult()  # no term_references collected (outage path)
    service._compute_identity({"category": "M"}, TERM_TEMPLATE, result)
    assert not result.valid
    assert result.errors[0]["code"] == "identity_resolution_failed"


def test_compute_identity_feeds_referenced_document_id():
    template = {
        "identity_fields": ["source_ref", "target_ref"],
        "fields": [
            {"name": "source_ref", "type": "reference", "reference_type": "document"},
            {"name": "target_ref", "type": "reference", "reference_type": "document"},
        ],
    }
    service = ValidationService()
    result = ValidationResult()
    result.references.extend([
        {"field_path": "source_ref", "reference_type": "document",
         "resolved": {"document_id": "D-source"}},
        {"field_path": "target_ref", "reference_type": "document",
         "resolved": {"document_id": "D-target"}},
    ])
    service._compute_identity(
        {"source_ref": "monster by any name", "target_ref": "hash:abc"},
        template, result,
    )
    assert result.valid
    assert result.identity_values == {
        "source_ref": "D-source", "target_ref": "D-target",
    }


def test_compute_identity_plain_fields_unchanged():
    template = {
        "identity_fields": ["email"],
        "fields": [{"name": "email", "type": "string"}],
    }
    service = ValidationService()
    result = ValidationResult()
    service._compute_identity({"email": "Alice@Example.com"}, template, result)
    assert result.valid
    assert result.identity_values == {"email": "Alice@Example.com"}
    assert result.submitted_identity_values == result.identity_values
