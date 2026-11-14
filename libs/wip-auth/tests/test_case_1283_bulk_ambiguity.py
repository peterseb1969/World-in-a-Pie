"""A bulk write door reports an ambiguous identifier as ambiguous, per item.

Provenance: CASE-1283 (review of its first fix).

Both doors of resolve_bulk_ids left an identifier several entities bear
unresolved, exactly like one nothing bears: the field door caught the
ambiguity as the not-found error it subclasses, the string door's non-strict
batch dropped it. The store then reported the raw value as not found. Now
resolve_bulk_ids returns the ambiguous items, and AmbiguousItems holds them
out of the write and answers each as ambiguous, naming the candidates.
"""

from types import SimpleNamespace

import pytest

from wip_auth.bulk_models import TerminologyTermBulkResponse, TerminologyTermBulkResultItem
from wip_auth.fastapi_helpers import AmbiguousItem, AmbiguousItems, resolve_bulk_ids
from wip_auth.resolve import AmbiguousEntityError, clear_resolution_cache

RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"
ONE = "019a0000-0000-7000-8000-000000000001"
TWO = "019a0000-0000-7000-8000-000000000002"
THREE = "019a0000-0000-7000-8000-000000000003"
CANDIDATES = [
    {"entry_id": ONE, "namespace": "paint", "entity_type": "terms", "matched_via": "synonym"},
    {"entry_id": TWO, "namespace": "paint", "entity_type": "terms", "matched_via": "synonym"},
]


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    monkeypatch.setenv("REGISTRY_API_KEY", "test-key")
    clear_resolution_cache()
    yield
    clear_resolution_cache()


def _ambiguous() -> dict:
    return {"status": "ambiguous", "entry_id": None, "candidates": CANDIDATES}


@pytest.mark.asyncio
async def test_the_field_door_returns_an_ambiguous_item(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json={"results": [_ambiguous()]})
    items = [SimpleNamespace(source_term_id="red", source_terminology="COLORS")]

    ambiguous = await resolve_bulk_ids(
        items, "source_term_id", "term", "paint",
        terminology_field="source_terminology", bypass_cache=True,
    )

    assert list(ambiguous) == [0]
    assert ambiguous[0].field == "source_term_id"
    assert isinstance(ambiguous[0].error, AmbiguousEntityError)
    assert items[0].source_term_id == "red", "an ambiguous value is never picked"


@pytest.mark.asyncio
async def test_the_string_door_returns_an_ambiguous_item(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json={"results": [
        {"status": "found", "entry_id": THREE}, _ambiguous(),
    ]})
    items = [{"template_id": "SHAPE"}, {"template_id": "COLOR"}]

    ambiguous = await resolve_bulk_ids(items, "template_id", "template", "paint", bypass_cache=True)

    assert list(ambiguous) == [1]
    assert items == [{"template_id": THREE}, {"template_id": "COLOR"}]


def test_held_items_are_answered_as_ambiguous_at_their_own_index():
    error = AmbiguousEntityError("red", "term", CANDIDATES, 2)
    items = ["a", "b", "c"]
    held = AmbiguousItems(items, {1: AmbiguousItem("source_term_id", error)}, {})
    assert held.items == ["a", "c"]

    response = held.answer(TerminologyTermBulkResponse(
        results=[
            TerminologyTermBulkResultItem(index=0, status="created", id="x"),
            TerminologyTermBulkResultItem(index=1, status="created", id="y"),
        ],
        total=2, succeeded=2, failed=0,
    ))

    assert [(r.index, r.status) for r in response.results] == [
        (0, "created"), (1, "error"), (2, "created"),
    ]
    refused = response.results[1]
    assert refused.error_code == "ambiguous_reference"
    assert refused.error is not None and "ambiguous" in refused.error
    assert refused.details is not None
    assert refused.details["field"] == "source_term_id"
    assert refused.details["candidates"] == CANDIDATES
    assert (response.total, response.succeeded, response.failed) == (3, 2, 1)


def test_a_request_that_stops_at_its_first_failure_skips_what_follows():
    error = AmbiguousEntityError("COLOR", "template", CANDIDATES, 2)
    held = AmbiguousItems(
        ["a", "b", "c", "d"], {1: AmbiguousItem("template_id", error)}, stop_at_first=True,
    )
    assert held.items == ["a"]

    response = held.answer(TerminologyTermBulkResponse(
        results=[TerminologyTermBulkResultItem(index=0, status="created", id="x")],
        total=1, succeeded=1, failed=0,
    ))

    assert [(r.index, r.status, r.error_code) for r in response.results] == [
        (0, "created", None), (1, "error", "ambiguous_reference"),
        (2, "skipped", None), (3, "skipped", None),
    ]
    assert (response.total, response.succeeded, response.failed) == (4, 1, 1)


@pytest.mark.asyncio
async def test_an_unambiguous_request_is_answered_unchanged(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json={"results": [{"status": "found", "entry_id": ONE}]})
    items = [{"template_id": "SHAPE"}]
    held = AmbiguousItems(items, await resolve_bulk_ids(items, "template_id", "template", "paint"))
    response = TerminologyTermBulkResponse(results=[], total=0, succeeded=0, failed=0)
    assert held.items == [{"template_id": ONE}]
    assert held.answer(response) is response
