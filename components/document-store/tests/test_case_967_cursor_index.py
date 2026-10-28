"""Cursor pages of one template read one page of an index.

Provenance: CASE-967 — the reporting batch sync now pages the document list
by cursor. Without an index ending in _id, a cursor query either sorts every
matching document per page or walks the collection's _id index, examining
every interleaved document of other templates on the way. With
ns_template_status_id_idx a page examines one page of keys, wherever the
template's documents sit in _id order. Checked against the real test MongoDB.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from bson import ObjectId

from document_store.models.document import Document

NS = "c967"
TPL_A = "0190c967-0000-7000-0000-00000000000a"
TPL_B = "0190c967-0000-7000-0000-00000000000b"
PAGE = 50


@pytest_asyncio.fixture(autouse=True)
async def _init_beanie(session_mongo_client):
    from tests.conftest import _ensure_beanie
    await _ensure_beanie(session_mongo_client)
    await Document.find({"namespace": NS}).delete()
    yield
    await Document.find({"namespace": NS}).delete()


def _doc(i: int, template_id: str) -> Document:
    return Document(
        namespace=NS,
        document_id=f"c967-{template_id[-1]}-{i:04d}",
        template_id=template_id,
        template_version=1,
        template_value="C967",
        identity_hash=f"h-{template_id[-1]}-{i}",
        version=1,
        data={"n": i},
        status="active",
    )


def _plan_indexes(plan: dict) -> list[str]:
    names, node = [], plan
    while node:
        if node.get("indexName"):
            names.append(node["indexName"])
        node = node.get("inputStage") or (node.get("inputStages") or [None])[0]
    return names


@pytest.mark.asyncio
async def test_the_cursor_index_exists():
    info = await Document.get_motor_collection().index_information()
    assert "ns_template_status_id_idx" in info
    assert [k for k, _ in info["ns_template_status_id_idx"]["key"]] == [
        "namespace", "template_id", "status", "_id",
    ]


@pytest.mark.asyncio
async def test_a_deep_cursor_page_examines_one_page():
    # Interleave two templates in _id order: walking the _id index for one
    # template's page would have to step over the other's documents.
    docs = []
    for i in range(300):
        docs.append(_doc(i, TPL_A))
        docs.append(_doc(i, TPL_B))
    await Document.insert_many(docs)

    a_ids = [d["_id"] async for d in Document.get_motor_collection().find(
        {"namespace": NS, "template_id": TPL_A}, {"_id": 1}).sort("_id", 1)]
    cursor = a_ids[150]  # a page in the middle

    query = {"namespace": NS, "template_id": TPL_A, "status": "active",
             "_id": {"$gt": ObjectId(cursor)}}
    explained = await Document.get_motor_collection().find(query).sort(
        "_id", 1).limit(PAGE).explain()
    stats = explained["executionStats"]

    assert "ns_template_status_id_idx" in _plan_indexes(explained["queryPlanner"]["winningPlan"])
    assert stats["nReturned"] == PAGE
    assert stats["totalDocsExamined"] == PAGE
    assert stats["totalKeysExamined"] <= PAGE + 1
