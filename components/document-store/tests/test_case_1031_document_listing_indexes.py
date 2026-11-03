"""One template's documents are listed from an index, whoever the caller.

The list endpoint's default order is created_at descending, then document_id
(build_sort_clauses always appends the tiebreaker). The template index ended
at created_at, so every page — namespaced or not — read and sorted the
template's whole document set in memory. A caller with no namespace filter
(a superadmin key, a whole-instance reporting sync) had no template-led index
for its cursor pages and counts at all. Checked against the real test
MongoDB:

  - The default listing for one template reads one page, with no SORT stage.
  - A namespace-less cursor page reads one page of the template's keys, not
    the other templates' documents interleaved in _id order.
  - A namespace-less count is served from an index.
  - A listing with no status filter takes the same index paths: "any
    status" is spelled as every status, which MongoDB merges as sorted
    ranges, and the listing still returns active and inactive documents.
  - The superseded template/time index is dropped at startup.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from bson import ObjectId

from document_store.main import drop_superseded_document_indexes
from document_store.models.document import Document
from document_store.services.document_service import DocumentService, name_every_status

NS = "c1031"
TPL_A = "0190c103-1000-7000-0000-00000000000a"
TPL_B = "0190c103-1000-7000-0000-00000000000b"
PAGE = 50
BASE = datetime(2026, 1, 1, tzinfo=UTC)


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
        document_id=f"c1031-{template_id[-1]}-{i:04d}",
        template_id=template_id,
        template_version=1,
        template_value="C1031",
        identity_hash=f"h-{template_id[-1]}-{i}",
        version=1,
        data={"n": i},
        status="active",
        # Pairs share a timestamp, so the document_id tiebreaker matters.
        created_at=BASE + timedelta(seconds=i // 2),
    )


def _stages(explained: dict) -> list[tuple[str, str | None]]:
    winning = explained["queryPlanner"]["winningPlan"]
    winning = winning.get("queryPlan", winning)
    out: list[tuple[str, str | None]] = []

    def walk(node: dict) -> None:
        out.append((node["stage"], node.get("indexName")))
        if "inputStage" in node:
            walk(node["inputStage"])
        for child in node.get("inputStages", []):
            walk(child)

    walk(winning)
    return out


async def _interleaved() -> None:
    docs = []
    for i in range(300):
        docs.append(_doc(i, TPL_A))
        docs.append(_doc(i, TPL_B))
    await Document.insert_many(docs)


@pytest.mark.asyncio
async def test_default_listing_reads_one_page_without_sorting():
    await _interleaved()
    coll = Document.get_motor_collection()
    # The shape list_documents sends for a namespaced caller.
    explained = await coll.find(
        {"namespace": {"$in": [NS]}, "template_id": TPL_A, "status": "active"}
    ).sort([("created_at", -1), ("document_id", 1)]).limit(PAGE).explain()
    stages = _stages(explained)
    assert ("IXSCAN", "ns_template_status_time_doc_idx") in stages, stages
    assert all(s != "SORT" for s, _ in stages), stages
    assert explained["executionStats"]["totalDocsExamined"] == PAGE


@pytest.mark.asyncio
async def test_namespace_less_cursor_page_reads_one_page():
    await _interleaved()
    coll = Document.get_motor_collection()
    a_ids = [d["_id"] async for d in coll.find({"template_id": TPL_A}, {"_id": 1}).sort("_id", 1)]
    query = {"template_id": TPL_A, "status": "active", "_id": {"$gt": ObjectId(a_ids[150])}}
    explained = await coll.find(query).sort("_id", 1).limit(PAGE).explain()
    stats = explained["executionStats"]
    assert ("IXSCAN", "template_status_id_idx") in _stages(explained)
    assert stats["nReturned"] == PAGE
    assert stats["totalDocsExamined"] == PAGE


@pytest.mark.asyncio
async def test_namespace_less_count_is_index_served():
    await _interleaved()
    coll = Document.get_motor_collection()
    explained = await coll.database.command(
        "explain",
        {"count": coll.name, "query": {"template_id": TPL_A, "status": "active"}},
        verbosity="executionStats",
    )
    assert all(s != "COLLSCAN" for s, _ in _stages(explained))
    assert explained["executionStats"]["totalDocsExamined"] == 0


@pytest.mark.asyncio
async def test_superseded_time_index_is_dropped():
    coll = Document.get_motor_collection()
    await coll.create_index(
        [("namespace", 1), ("template_id", 1), ("status", 1), ("created_at", -1)],
        name="ns_template_status_time_idx",
    )
    await drop_superseded_document_indexes(coll.database)
    names = set(await coll.index_information())
    assert "ns_template_status_time_idx" not in names
    assert {"ns_template_status_time_doc_idx", "template_status_id_idx"} <= names
    # A second run on a database without it is a no-op.
    await drop_superseded_document_indexes(coll.database)


def test_any_status_is_named_only_on_one_template_queries():
    q = {"template_id": TPL_A}
    name_every_status(q)
    assert q["status"] == {"$in": ["active", "inactive"]}

    explicit = {"template_id": TPL_A, "status": "inactive"}
    name_every_status(explicit)
    assert explicit["status"] == "inactive"

    unscoped = {"namespace": NS}
    name_every_status(unscoped)
    assert "status" not in unscoped


async def _listing_queries(monkeypatch, **kwargs) -> tuple[list[dict], object]:
    """Run list_documents and capture the filters it sends to Document.find."""
    seen: list[dict] = []
    original = Document.find.__func__

    def recording_find(cls, *args, **kw):
        seen.append(args[0])
        return original(cls, *args, **kw)

    monkeypatch.setattr(Document, "find", classmethod(recording_find))
    result = await DocumentService().list_documents(template_id=TPL_A, **kwargs)
    return seen, result


@pytest.mark.asyncio
async def test_unfiltered_listing_is_index_served_and_returns_every_status(monkeypatch):
    await _interleaved()
    await Document.get_motor_collection().update_many(
        {"namespace": NS, "template_id": TPL_A, "data.n": {"$lt": 10}},
        {"$set": {"status": "inactive"}},
    )
    coll = Document.get_motor_collection()

    seen, result = await _listing_queries(
        monkeypatch, ns_filter={"namespace": {"$in": [NS]}}, page_size=PAGE
    )
    assert result.total == 300
    query = seen[-1]
    explained = await coll.find(query).sort(
        [("created_at", -1), ("document_id", 1)]).limit(PAGE).explain()
    stages = _stages(explained)
    assert all(s != "SORT" for s, _ in stages), stages
    assert explained["executionStats"]["totalDocsExamined"] == PAGE

    # Thin template A out in _id order, so walking the _id index would step
    # over many other documents per page of A.
    await Document.find({"namespace": NS}).delete()
    sparse = []
    for i in range(300):
        sparse.append(_doc(i, TPL_A))
        sparse.extend(_doc(1000 + 9 * i + j, TPL_B) for j in range(9))
    await Document.insert_many(sparse)
    seen, result = await _listing_queries(
        monkeypatch, cursor="000000000000000000000000", page_size=PAGE
    )
    assert len(result.items) == PAGE
    explained = await coll.find(seen[-1]).sort("_id", 1).limit(PAGE).explain()
    assert ("IXSCAN", "template_status_id_idx") in _stages(explained)
    assert explained["executionStats"]["totalDocsExamined"] == PAGE
