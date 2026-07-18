"""Metadata is non-identity document content and versions like data.

Covers the two halves of the contract:

1. Create/upsert — a metadata-only delta on identical data is a real change
   (new version), never a silent drop. ``metadata`` omitted means "not
   addressed" (carries forward, both in the change gate and on the write);
   a supplied object — including ``{}`` — replaces wholesale.
2. PATCH — ``metadata_patch`` merge-patches ``metadata.custom`` (RFC 7396),
   participates in no-op detection, and mints a normal new version.

Metadata never feeds the identity hash: none of these writes may create a
second document.
"""

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def create_one(
    client: AsyncClient,
    auth_headers: dict,
    template_id: str,
    data: dict,
    **extra,
):
    """Create/upsert a single document via the bulk-first POST endpoint."""
    payload = {"namespace": "wip", "template_id": template_id, "data": data, **extra}
    response = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[payload],
    )
    assert response.status_code == 200, f"Create failed: {response.text}"
    bulk = response.json()
    return bulk["results"][0]


async def patch_one(
    client: AsyncClient,
    auth_headers: dict,
    document_id: str,
    patch: dict,
    metadata_patch: dict | None = None,
    if_match: int | None = None,
):
    """Send a single PATCH item and return its result."""
    item: dict = {"document_id": document_id, "patch": patch}
    if metadata_patch is not None:
        item["metadata_patch"] = metadata_patch
    if if_match is not None:
        item["if_match"] = if_match
    response = await client.patch(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[item],
    )
    assert response.status_code == 200, f"PATCH failed: {response.text}"
    return response.json()["results"][0]


async def get_doc(client: AsyncClient, auth_headers: dict, document_id: str) -> dict:
    resp = await client.get(
        f"/api/document-store/documents/{document_id}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, f"GET failed: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Create/upsert path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_metadata_only_delta_creates_new_version(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """Identical data + changed metadata is a real change — persisted, versioned."""
    initial = await create_one(
        client, auth_headers, "PERSON", sample_person_data,
        metadata={"session_id": "SESSION-A"},
    )
    assert initial["status"] == "created"
    assert initial["version"] == 1

    result = await create_one(
        client, auth_headers, "PERSON", sample_person_data,
        metadata={"session_id": "SESSION-B"},
    )
    assert result["status"] == "updated"
    assert result["version"] == 2
    assert result["document_id"] == initial["document_id"]  # no dedup break

    doc = await get_doc(client, auth_headers, initial["document_id"])
    assert doc["metadata"]["custom"] == {"session_id": "SESSION-B"}


@pytest.mark.asyncio
async def test_upsert_identical_data_and_metadata_is_unchanged(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """Re-sending the same data + same metadata stays a no-op."""
    initial = await create_one(
        client, auth_headers, "PERSON", sample_person_data,
        metadata={"source": "loader"},
    )
    result = await create_one(
        client, auth_headers, "PERSON", sample_person_data,
        metadata={"source": "loader"},
    )
    assert result["status"] == "unchanged"
    assert result["version"] == initial["version"]


@pytest.mark.asyncio
async def test_upsert_omitted_metadata_is_not_addressed(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """metadata omitted = no opinion: unchanged data stays a no-op and the
    stored metadata survives."""
    initial = await create_one(
        client, auth_headers, "PERSON", sample_person_data,
        metadata={"source": "loader"},
    )
    result = await create_one(client, auth_headers, "PERSON", sample_person_data)
    assert result["status"] == "unchanged"
    assert result["version"] == initial["version"]

    doc = await get_doc(client, auth_headers, initial["document_id"])
    assert doc["metadata"]["custom"] == {"source": "loader"}


@pytest.mark.asyncio
async def test_upsert_data_change_with_omitted_metadata_carries_custom_forward(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """A data-changing upsert that does not address metadata must not wipe it."""
    initial = await create_one(
        client, auth_headers, "PERSON", sample_person_data,
        metadata={"source": "loader"},
    )
    changed = {**sample_person_data, "first_name": "Jane"}
    result = await create_one(client, auth_headers, "PERSON", changed)
    assert result["status"] == "updated"
    assert result["version"] == 2

    doc = await get_doc(client, auth_headers, initial["document_id"])
    assert doc["data"]["first_name"] == "Jane"
    assert doc["metadata"]["custom"] == {"source": "loader"}


@pytest.mark.asyncio
async def test_upsert_explicit_empty_metadata_clears(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """An explicitly supplied {} is intent — it clears custom metadata (and,
    being a change, mints a version)."""
    initial = await create_one(
        client, auth_headers, "PERSON", sample_person_data,
        metadata={"source": "loader"},
    )
    result = await create_one(
        client, auth_headers, "PERSON", sample_person_data, metadata={},
    )
    assert result["status"] == "updated"
    assert result["version"] == initial["version"] + 1

    doc = await get_doc(client, auth_headers, initial["document_id"])
    assert doc["metadata"]["custom"] == {}


# ---------------------------------------------------------------------------
# PATCH path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_metadata_only_creates_new_version(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """patch={} + metadata_patch is a metadata-only update: new version,
    data untouched, metadata merged."""
    initial = await create_one(
        client, auth_headers, "PERSON", sample_person_data,
        metadata={"source": "loader"},
    )

    result = await patch_one(
        client, auth_headers, initial["document_id"], {},
        metadata_patch={"reviewed_by": "peter"},
    )
    assert result["status"] == "updated"
    assert result["version"] == 2
    assert result["document_id"] == initial["document_id"]

    doc = await get_doc(client, auth_headers, initial["document_id"])
    assert doc["data"]["first_name"] == sample_person_data["first_name"]
    assert doc["metadata"]["custom"] == {"source": "loader", "reviewed_by": "peter"}


@pytest.mark.asyncio
async def test_patch_metadata_null_deletes_key(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """RFC 7396: null in metadata_patch deletes the key from custom."""
    initial = await create_one(
        client, auth_headers, "PERSON", sample_person_data,
        metadata={"source": "loader", "tag": "alpha"},
    )

    result = await patch_one(
        client, auth_headers, initial["document_id"], {},
        metadata_patch={"tag": None},
    )
    assert result["status"] == "updated"

    doc = await get_doc(client, auth_headers, initial["document_id"])
    assert doc["metadata"]["custom"] == {"source": "loader"}


@pytest.mark.asyncio
async def test_patch_noop_metadata_patch_is_unchanged(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """A metadata_patch that reproduces the current custom is a no-op."""
    initial = await create_one(
        client, auth_headers, "PERSON", sample_person_data,
        metadata={"source": "loader"},
    )

    result = await patch_one(
        client, auth_headers, initial["document_id"], {},
        metadata_patch={"source": "loader"},
    )
    assert result["status"] == "unchanged"
    assert result["version"] == initial["version"]


@pytest.mark.asyncio
async def test_patch_data_and_metadata_together(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict
):
    """Data patch and metadata patch land in the same single new version."""
    initial = await create_one(
        client, auth_headers, "PERSON", sample_person_data,
        metadata={"source": "loader"},
    )

    result = await patch_one(
        client, auth_headers, initial["document_id"],
        {"first_name": "Jane"},
        metadata_patch={"reviewed_by": "peter"},
    )
    assert result["status"] == "updated"
    assert result["version"] == 2

    doc = await get_doc(client, auth_headers, initial["document_id"])
    assert doc["data"]["first_name"] == "Jane"
    assert doc["metadata"]["custom"] == {"source": "loader", "reviewed_by": "peter"}
