"""A bulk create whose items interleave namespaces writes one batch per
namespace when it continues past errors (the default), not one batch per
run of consecutive items; with continue_on_error=false it keeps the runs, so
it still stops at the first failing item in request order."""

from typing import Any

import pytest

from document_store.api import documents as docs_api
from document_store.models.api_models import DocumentCreateRequest


def _items(*namespaces: str) -> list[DocumentCreateRequest]:
    return [
        DocumentCreateRequest(namespace=ns, template_id="T", data={"n": i})
        for i, ns in enumerate(namespaces)
    ]


def test_continuing_groups_by_namespace_in_submission_order():
    assert docs_api._namespace_batches(_items("a", "b", "a", "b", "a"), True) == [
        [0, 2, 4], [1, 3],
    ]


def test_stopping_keeps_consecutive_runs():
    assert docs_api._namespace_batches(_items("a", "b", "a"), False) == [[0], [1], [2]]


@pytest.mark.asyncio
async def test_rows_land_at_their_request_positions():
    from document_store.models.api_models import BulkResponse, BulkResultItem

    calls: list[tuple[str, int]] = []

    class Service:
        async def bulk_create(self, items: list[Any], namespace: str, continue_on_error: bool):
            calls.append((namespace, len(items)))
            rows = [BulkResultItem(index=i, status="created", id=f"{namespace}-{item.data['n']}")
                    for i, item in enumerate(items)]
            return BulkResponse(results=rows, total=len(rows), succeeded=len(rows), failed=0)

    result = await docs_api._bulk_create_per_namespace(
        Service(), _items("a", "b", "a", "b"), continue_on_error=True,
    )
    assert calls == [("a", 2), ("b", 2)]
    assert [(r.index, r.id) for r in result.results] == [(0, "a-0"), (1, "b-1"), (2, "a-2"), (3, "b-3")]
