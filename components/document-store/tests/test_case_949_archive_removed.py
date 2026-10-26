"""Document archive is removed; what it left behind is soft delete.

Archive was designed as policy-driven tiering of superseded versions, but what
shipped was a manual archive of the latest version that behaved as a second
soft delete. It is gone:
- the archive route answers 404/405;
- rows still carrying `archived` are rewritten to `inactive`, both at startup
  and as a restore writes them;
- an edge whose endpoint is soft-deleted is accepted with a warning, the rule
  a plain document reference to an inactive document follows.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.archived_status import (
    retire_archived_row,
    retire_archived_status,
)
from document_store.services.backup_engine import COLLECTION_MAP, DirectRestoreEngine

API = "/api/document-store"
NAMESPACE = "archive-removed-949"


@pytest_asyncio.fixture
async def documents():
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))
    db_name, coll_name = COLLECTION_MAP["documents"]
    collection = client[db_name][coll_name]
    await collection.delete_many({"namespace": NAMESPACE})
    yield collection
    await collection.delete_many({"namespace": NAMESPACE})
    client.close()


@pytest.mark.asyncio
async def test_the_archive_route_is_gone(client: AsyncClient, auth_headers: dict):
    resp = await client.post(
        f"{API}/documents/archive", headers=auth_headers, json=[{"id": "x"}]
    )
    assert resp.status_code in (404, 405)


@pytest.mark.asyncio
async def test_startup_rewrites_archived_rows_as_inactive(documents):
    await documents.insert_many([
        {"namespace": NAMESPACE, "document_id": "D1", "version": 1, "status": "inactive"},
        {"namespace": NAMESPACE, "document_id": "D1", "version": 2, "status": "archived"},
        {"namespace": NAMESPACE, "document_id": "D2", "version": 1, "status": "active"},
    ])

    assert await retire_archived_status(documents) == 1
    # Idempotent: nothing left to rewrite.
    assert await retire_archived_status(documents) == 0

    statuses = {
        (r["document_id"], r["version"]): r["status"]
        async for r in documents.find({"namespace": NAMESPACE})
    }
    assert statuses == {("D1", 1): "inactive", ("D1", 2): "inactive", ("D2", 1): "active"}


def test_a_restored_row_is_rewritten_and_others_are_left_alone():
    archived = {"document_id": "D1", "status": "archived"}
    active = {"document_id": "D2", "status": "active"}
    retire_archived_row(archived)
    retire_archived_row(active)
    assert archived["status"] == "inactive"
    assert active["status"] == "active"


@pytest.mark.asyncio
async def test_the_restore_door_writes_archived_documents_as_inactive():
    # An archive taken before the removal still carries the status; the
    # insert path rewrites it so a restore cannot bring the state back.
    collection = MagicMock()
    collection.insert_many = AsyncMock()
    engine = DirectRestoreEngine(MagicMock(), None, lambda _e: None)

    await engine._insert_batch(
        collection,
        [{"document_id": "D1", "version": 1, "status": "archived"}],
        "documents",
    )

    [written] = collection.insert_many.call_args[0][0]
    assert written["status"] == "inactive"


@pytest.mark.asyncio
async def test_an_edge_to_a_deleted_endpoint_is_accepted_with_a_warning(
    client: AsyncClient, auth_headers: dict
):
    created = (await client.post(f"{API}/documents", headers=auth_headers, json=[
        {"namespace": "wip", "template_id": "EXPERIMENT",
         "data": {"experiment_id": "EXP-949", "name": "x"}},
        {"namespace": "wip", "template_id": "MOLECULE",
         "data": {"molecule_id": "MOL-949", "name": "y"}},
    ])).json()["results"]
    exp_id, mol_id = created[0]["document_id"], created[1]["document_id"]
    deleted = await client.request(
        "DELETE", f"{API}/documents", headers=auth_headers, json=[{"id": mol_id}]
    )
    assert deleted.json()["results"][0]["status"] == "deleted"

    edge = (await client.post(f"{API}/documents", headers=auth_headers, json=[
        {"namespace": "wip", "template_id": "EXPERIMENT_INPUT",
         "data": {"source_ref": exp_id, "target_ref": mol_id}},
    ])).json()["results"][0]

    assert edge["status"] == "created", edge
    assert any("inactive" in w for w in edge["warnings"]), edge["warnings"]
