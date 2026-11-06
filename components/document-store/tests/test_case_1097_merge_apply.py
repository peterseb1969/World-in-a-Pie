"""Merge apply: what the merge writes is what the services would have written
(CASE-1097).

1. File reference counts follow the documents the merge writes.
2. An overwrite in place (versioned: false) stores dates as dates.
3. A batch insert that fails partway never leaves two active versions.
4. An overwrite whose archive copy equals the target's adds no version.
5. The reporting-structure gate runs before documents move.

The real engine against the real test MongoDB, archive stubbed, as in
test_merge_restore.py (whose helpers these tests use).
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import patch

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from document_store.services.backup_engine import (
    COLLECTION_MAP,
    DirectRestoreEngine,
    RestoreEngineError,
)
from tests.test_merge_restore import (
    NAMESPACE,
    _all_collections,
    _archive,
    _document,
    _rows,
    _run_merge,
    _seed,
    _seed_namespace,
    _template,
)


@pytest_asyncio.fixture
async def mongo(session_mongo_client):
    """A real client, with this suite's namespace cleared before and after."""
    from tests.conftest import _ensure_beanie

    await _ensure_beanie(session_mongo_client)
    client = AsyncIOMotorClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/"))

    async def _clear():
        for db_name, coll_name in _all_collections():
            key = "prefix" if coll_name == "namespaces" else "namespace"
            await client[db_name][coll_name].delete_many({key: NAMESPACE})

    await _clear()
    yield client
    await _clear()
    client.close()


def _file(file_id, count=0):
    return {
        "file_id": file_id,
        "namespace": NAMESPACE,
        "filename": f"{file_id}.bin",
        "status": "active",
        "reference_count": count,
    }


def _doc(version, *, doc_id, ident, data=None, files=()):
    row = _document(version, doc_id=doc_id, data=data)
    row["identity_hash"] = ident
    row["file_references"] = [{"field_path": "f", "file_id": f} for f in files]
    return row


async def _counts(mongo):
    return {f["file_id"]: f["reference_count"] for f in await _rows(mongo, "files")}


async def _active(mongo, doc_id):
    return sorted(
        d["version"] for d in await _rows(mongo, "documents")
        if d["document_id"] == doc_id and d["status"] == "active"
    )


# ── 1. reference counts ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_merged_in_document_counts_as_a_reference(mongo):
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_template()])
    await _seed(mongo, "files", [_file("F1")])

    await _run_merge(mongo, _archive({
        "documents": [_doc(1, doc_id="D2", ident="h2", files=["F1"])],
    }))

    assert (await _counts(mongo))["F1"] == 1


@pytest.mark.asyncio
async def test_an_overwrite_moves_the_count_to_the_new_heads_files(mongo):
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_template()])
    await _seed(mongo, "files", [_file("F1", count=1), _file("F2")])
    await _seed(mongo, "documents", [_doc(1, doc_id="D1", ident="h1", files=["F1"])])

    await _run_merge(mongo, _archive({
        "documents": [_doc(1, doc_id="D1", ident="h1", data={"n": "new"}, files=["F2"])],
    }), on_clash="overwrite", older_versions="keep")

    # Counts follow each document's latest version, as document writes do.
    assert await _counts(mongo) == {"F1": 0, "F2": 1}


# ── 2. dates on an overwrite in place ─────────────────────────────────


@pytest.mark.asyncio
async def test_an_overwrite_in_place_stores_dates_as_dates(mongo):
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_template(versioned=False)])
    await _seed(mongo, "documents", [_doc(1, doc_id="D1", ident="h1")])
    incoming = _doc(1, doc_id="D1", ident="h1", data={"n": "archive"})
    incoming["created_at"] = "2026-01-02T03:04:05+00:00"
    incoming["updated_at"] = "2026-01-02T03:04:05+00:00"

    await _run_merge(mongo, _archive({"documents": [incoming]}), on_clash="overwrite")

    [doc] = await _rows(mongo, "documents")
    assert doc["data"]["n"] == "archive"
    assert isinstance(doc["created_at"], datetime)
    assert isinstance(doc["updated_at"], datetime)


# ── 3. a partial insert ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_partial_insert_never_leaves_two_active_versions(mongo):
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_template()])
    await _seed(mongo, "documents", [
        _doc(1, doc_id="D1", ident="h1"),
        _doc(1, doc_id="D2", ident="h2"),
    ])
    real = DirectRestoreEngine._insert_batch

    async def first_row_then_fail(self, collection, batch, entity_type):
        if entity_type == "documents" and len(batch) > 1:
            await real(self, collection, batch[:1], entity_type)
            raise RestoreEngineError("Bulk insert failed for documents: 1 errors out of 2")
        await real(self, collection, batch, entity_type)

    with (
        patch.object(DirectRestoreEngine, "_insert_batch", first_row_then_fail),
        pytest.raises(RestoreEngineError, match="Bulk insert failed"),
    ):
        await _run_merge(mongo, _archive({"documents": [
            _doc(1, doc_id="D1", ident="h1", data={"n": "a1"}),
            _doc(1, doc_id="D2", ident="h2", data={"n": "a2"}),
        ]}), on_clash="overwrite", older_versions="retire")

    landed = {d["document_id"] for d in await _rows(mongo, "documents") if d["version"] == 2}
    assert len(landed) == 1
    for doc_id in ("D1", "D2"):
        assert len(await _active(mongo, doc_id)) == 1, doc_id


# ── 4. identical overwrites ───────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("versioned", [True, False])
async def test_an_identical_overwrite_adds_no_version(mongo, versioned):
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_template(versioned=versioned)])
    await _seed(mongo, "documents", [_doc(1, doc_id="D1", ident="h1", data={"n": "same"})])
    before = await _rows(mongo, "documents")

    await _run_merge(mongo, _archive({
        "documents": [_doc(1, doc_id="D1", ident="h1", data={"n": "same"})],
    }), on_clash="overwrite")

    assert await _rows(mongo, "documents") == before


@pytest.mark.asyncio
async def test_a_different_overwrite_still_adds_a_version(mongo):
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_template()])
    await _seed(mongo, "documents", [_doc(1, doc_id="D1", ident="h1", data={"n": "old"})])

    await _run_merge(mongo, _archive({
        "documents": [_doc(1, doc_id="D1", ident="h1", data={"n": "new"})],
    }), on_clash="overwrite")

    assert sorted(d["version"] for d in await _rows(mongo, "documents")) == [1, 2]


# ── 5. the reporting gate ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_reporting_gate_runs_before_documents_move(mongo):
    await _seed_namespace(mongo)
    await _seed(mongo, "templates", [_template()])
    seen: list[int] = []

    async def gate(self, namespace):
        db_name, coll_name = COLLECTION_MAP["documents"]
        seen.append(await self._mongo[db_name][coll_name].count_documents({"namespace": namespace}))

    with patch.object(DirectRestoreEngine, "_reporting_phase_structure", gate):
        await _run_merge(mongo, _archive({
            "documents": [_doc(1, doc_id="D2", ident="h2")],
        }))

    assert seen == [0]
    assert len(await _rows(mongo, "documents")) == 1
