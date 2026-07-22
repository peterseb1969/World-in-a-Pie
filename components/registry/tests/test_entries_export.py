"""The raw-entries export surface (backup/export tooling).

Unlike browse and lookup (trimmed projections), export returns rows as
stored — hash, complete synonyms, search_values, status — because an
archive carrying anything less restores into a namespace where nothing
resolves. The CLI exporter writes these rows as the archive's
registry_entries file; the server restore engine re-inserts them verbatim.
"""

import pytest
from httpx import AsyncClient

EXPORT = "/api/registry/entries/export"


async def _register_entry(
    client: AsyncClient,
    auth_headers: dict,
    namespace: str = "default",
    entity_type: str = "terms",
    composite_key: dict | None = None,
) -> str:
    payload = {
        "namespace": namespace,
        "entity_type": entity_type,
        "composite_key": composite_key or {"value": "export-probe"},
    }
    resp = await client.post(
        "/api/registry/entries/register", json=[payload], headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]["registry_id"]


@pytest.mark.asyncio
async def test_export_returns_the_full_raw_row(client: AsyncClient, auth_headers: dict):
    entry_id = await _register_entry(
        client, auth_headers, composite_key={"value": "raw-row-probe"},
    )
    # A custom synonym must ride along in the exported row.
    resp = await client.post(
        "/api/registry/synonyms/add",
        json=[{
            "target_id": entry_id,
            "synonym_namespace": "default",
            "synonym_entity_type": "terms",
            "synonym_composite_key": {"legacy_code": "OLD-77"},
        }],
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        EXPORT, json={"entry_ids": [entry_id]}, headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["found"] == 1
    row = data["results"][0]["entry"]

    # The raw-row fields the trimmed surfaces omit — exactly what the
    # restore engine's verbatim insert needs.
    assert row["entry_id"] == entry_id
    assert row["primary_composite_key"] == {"value": "raw-row-probe"}
    assert row["primary_composite_key_hash"]
    assert row["status"] == "active"
    assert "raw-row-probe" in row["search_values"]
    synonym_keys = [s["composite_key"] for s in row["synonyms"]]
    assert {"legacy_code": "OLD-77"} in synonym_keys
    assert "_id" not in row and "id" not in row


@pytest.mark.asyncio
async def test_export_mixes_found_and_not_found_per_item(
    client: AsyncClient, auth_headers: dict
):
    entry_id = await _register_entry(
        client, auth_headers, composite_key={"value": "mix-probe"},
    )
    resp = await client.post(
        EXPORT,
        json={"entry_ids": ["GHOST-ID", entry_id]},
        headers=auth_headers,
    )
    data = resp.json()
    assert data["total"] == 2
    assert data["found"] == 1
    assert data["not_found"] == 1
    assert data["results"][0]["status"] == "not_found"
    assert data["results"][1]["status"] == "found"


@pytest.mark.asyncio
async def test_export_includes_inactive_rows(client: AsyncClient, auth_headers: dict):
    """An archive records the instance as it is — inactive rows included;
    the row's own status field carries the truth."""
    entry_id = await _register_entry(
        client, auth_headers, composite_key={"value": "inactive-probe"},
    )
    resp = await client.request(
        "DELETE", "/api/registry/entries",
        json=[{"entry_id": entry_id}], headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        EXPORT, json={"entry_ids": [entry_id]}, headers=auth_headers,
    )
    data = resp.json()
    assert data["found"] == 1
    assert data["results"][0]["entry"]["status"] == "inactive"


@pytest.mark.asyncio
async def test_export_caps_batch_size(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        EXPORT,
        json={"entry_ids": [f"id-{i}" for i in range(501)]},
        headers=auth_headers,
    )
    assert resp.status_code == 422
