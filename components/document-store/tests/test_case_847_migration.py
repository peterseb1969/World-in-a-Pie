"""The identity-hash migration pass (CASE-847 / DECISION-2, migration leg).

Documents written under spelling-fed hashing carry hashes the resolved-
referent feed no longer produces. The maintenance pass recomputes each
document's hash from its stored resolutions, updates Mongo, and re-keys the
Registry claim. Forked duplicates that now converge are collision-reported
and never applied — converging two version chains is curation, not
migration.
"""

import pytest
from httpx import AsyncClient

from document_store.models.document import Document
from wip_auth.document_identity import compute_hash


async def create_one(client: AsyncClient, auth_headers: dict, template_id: str, data: dict):
    payload = {"namespace": "wip", "template_id": template_id, "data": data}
    response = await client.post(
        "/api/document-store/documents", headers=auth_headers, json=[payload],
    )
    assert response.status_code == 200, response.text
    item = response.json()["results"][0]
    assert item["status"] in ("created", "updated"), item
    return item


async def _rehash(client, auth_headers, dry_run: bool):
    response = await client.post(
        "/api/document-store/maintenance/rehash-identities",
        headers=auth_headers,
        json={
            "namespace": "wip",
            "dry_run": dry_run,
            "template_values": ["TERM_IDENTITY"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_migration_rehashes_legacy_spelling_fed_documents(
    client: AsyncClient, auth_headers: dict,
):
    created = await create_one(
        client, auth_headers, "TERM_IDENTITY",
        {"category": "M", "note": "born resolved"},
    )
    doc_id = created["document_id"]
    resolved_hash = created["identity_hash"]

    # Forge the legacy state: the spelling-fed hash on every version row.
    legacy_hash = compute_hash({"category": "M"})
    assert legacy_hash != resolved_hash
    collection = Document.get_motor_collection()
    await collection.update_many(
        {"document_id": doc_id}, {"$set": {"identity_hash": legacy_hash}},
    )

    # Dry run: reported, not applied.
    report = await _rehash(client, auth_headers, dry_run=True)
    mine = next(i for i in report["items"] if i["document_id"] == doc_id)
    assert mine["status"] == "changed"
    assert mine["old_hash"] == legacy_hash
    assert mine["new_hash"] == resolved_hash
    row = await collection.find_one({"document_id": doc_id})
    assert row["identity_hash"] == legacy_hash, "dry run must not write"

    # Apply: Mongo rows move to the resolved hash; the Registry claim (which
    # was minted under the resolved feed at create time) reports unchanged.
    report = await _rehash(client, auth_headers, dry_run=False)
    mine = next(i for i in report["items"] if i["document_id"] == doc_id)
    assert mine["status"] == "changed"
    assert mine["versions_updated"] >= 1
    assert mine["registry_status"] in ("rekeyed", "unchanged"), mine
    row = await collection.find_one({"document_id": doc_id})
    assert row["identity_hash"] == resolved_hash


@pytest.mark.asyncio
async def test_migration_reports_collisions_and_refuses_to_apply_them(
    client: AsyncClient, auth_headers: dict,
):
    created = await create_one(
        client, auth_headers, "TERM_IDENTITY",
        {"category": "F", "note": "the original"},
    )
    doc_id = created["document_id"]
    resolved_hash = created["identity_hash"]

    # Forge a pre-rule fork: a second document for the same term, written
    # under an alias spelling and hashed from it.
    collection = Document.get_motor_collection()
    row = await collection.find_one({"document_id": doc_id})
    fork = dict(row)
    fork.pop("_id")
    fork_id = "0199ffff-0000-7000-8000-000000000847"
    fork["document_id"] = fork_id
    fork["data"] = {**row["data"], "category": "f", "note": "the alias fork"}
    fork["identity_hash"] = compute_hash({"category": "f"})
    await collection.insert_one(fork)

    report = await _rehash(client, auth_headers, dry_run=True)
    group = next(
        (g for g in report["collision_groups"] if fork_id in g["document_ids"]),
        None,
    )
    assert group is not None, report["collision_groups"]
    assert doc_id in group["document_ids"]
    assert group["identity_hash"] == resolved_hash
    fork_item = next(i for i in report["items"] if i["document_id"] == fork_id)
    assert fork_item["status"] == "collision"

    # Apply refuses the colliding pair: the fork keeps its legacy hash.
    await _rehash(client, auth_headers, dry_run=False)
    fork_row = await collection.find_one({"document_id": fork_id})
    assert fork_row["identity_hash"] == compute_hash({"category": "f"})
