"""latest_only lists each document's latest version, without grouping every row.

The listing used to sort and group every matching row, then sort the groups,
twice per request (count and page): seconds on a namespace with a few hundred
thousand documents. It now finds the documents whose shape differs from the
usual one (one active row over superseded inactive rows) with one lean group,
and lists through a plain filter. The contract under test, against the real
test MongoDB, for every status filter:

  - The result is exactly each document's highest version, filtered by that
    row's status — the same rows the full group gives.
  - That holds for every document shape: live with superseded versions,
    soft-deleted, several active versions (a merge restore with
    ``older_versions: keep``), an active row under a higher inactive one,
    and revived after deletion.
  - Totals and pagination agree with the rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from document_store.models.document import Document, DocumentStatus
from document_store.services.document_service import DocumentService

NS = "c1039"
TPL = "0190c103-9000-7000-0000-00000000000a"
BASE = datetime(2026, 1, 1, tzinfo=UTC)

# document_id -> statuses of versions 1..n
SHAPES: dict[str, list[str]] = {
    "single": ["active"],
    "live": ["inactive", "inactive", "active"],
    "deleted": ["inactive", "inactive"],
    "deleted-once": ["inactive"],
    "kept": ["inactive", "active", "active"],      # merge with older_versions: keep
    "under": ["active", "inactive"],               # active row under a higher inactive one
    "revived": ["inactive", "inactive", "active"],  # deleted, then written again
}


@pytest_asyncio.fixture(autouse=True)
async def _init_beanie(session_mongo_client):
    from tests.conftest import _ensure_beanie
    await _ensure_beanie(session_mongo_client)
    await Document.find({"namespace": NS}).delete()
    rows = []
    for n, (doc_id, statuses) in enumerate(SHAPES.items()):
        for v, st in enumerate(statuses, start=1):
            rows.append(Document(
                namespace=NS, document_id=doc_id, template_id=TPL, template_version=1,
                template_value="C1039", identity_hash=f"h-{doc_id}", version=v,
                data={"doc": doc_id, "v": v}, status=st,
                created_at=BASE + timedelta(minutes=n),
                updated_at=BASE + timedelta(minutes=n, seconds=v),
            ))
    await Document.insert_many(rows)
    yield
    await Document.find({"namespace": NS}).delete()


def _expected(status: str | None) -> set[tuple[str, int]]:
    """The documented semantics: each document's highest version, then the status filter."""
    out = set()
    for doc_id, statuses in SHAPES.items():
        top = len(statuses)
        if status is None or statuses[top - 1] == status:
            out.add((doc_id, top))
    return out


async def _listed(status: DocumentStatus | None, page_size: int = 100, page: int = 1):
    result = await DocumentService().list_documents(
        ns_filter={"namespace": {"$in": [NS]}}, latest_only=True, status=status,
        page=page, page_size=page_size, sort_by="updated_at", sort_order="desc",
    )
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, DocumentStatus.ACTIVE, DocumentStatus.INACTIVE])
async def test_each_documents_latest_version(status):
    result = await _listed(status)
    got = {(d.document_id, d.version) for d in result.items}
    expected = _expected(status.value if status else None)
    assert got == expected
    assert result.total == len(expected)
    assert len(result.items) == len(expected)


@pytest.mark.asyncio
async def test_template_scoped_listing_agrees():
    result = await DocumentService().list_documents(
        template_id=TPL, ns_filter={"namespace": {"$in": [NS]}},
        latest_only=True, status=DocumentStatus.ACTIVE, page_size=100,
    )
    assert {(d.document_id, d.version) for d in result.items} == _expected("active")


@pytest.mark.asyncio
async def test_pages_cover_the_result_once():
    seen = []
    for page in (1, 2, 3, 4):
        result = await _listed(None, page_size=2, page=page)
        seen.extend((d.document_id, d.version) for d in result.items)
    assert len(seen) == len(set(seen))
    assert set(seen) == _expected(None)


@pytest.mark.asyncio
async def test_plain_shape_needs_no_exception_filter():
    """A namespace where every document has the usual shape lists through the
    plain active filter alone: no _id list in the query."""
    await Document.find({"namespace": NS, "document_id": {"$nin": ["single", "live"]}}).delete()
    query = await DocumentService._latest_versions_query(
        {"namespace": {"$in": [NS]}}, DocumentStatus.ACTIVE
    )
    assert query == {"namespace": {"$in": [NS]}, "status": "active"}
    query = await DocumentService._latest_versions_query({"namespace": {"$in": [NS]}}, None)
    assert query == {"namespace": {"$in": [NS]}, "status": "active"}
