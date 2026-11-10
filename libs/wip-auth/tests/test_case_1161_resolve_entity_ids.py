"""Batch entity resolution, pinned before resolve_entity_ids' refactor.

Provenance: CASE-1161. The suite never reached this branch: in a non-strict
batch, a miss whose by-id fallback finds nothing is followed by more
identifiers of the same chunk — they still resolve. Also pinned: a term miss
gets no fallback, and every answer lands in the cache under the request's
namespace and type, the fallback's included.
"""

import json

import pytest

from wip_auth.resolve import _entity_key, _get_cached, clear_resolution_cache, resolve_entity_ids

RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"
CANDIDATES_URL = "http://localhost:8001/api/registry/entries/lookup/candidates"
U1 = "01903f5a-0000-7000-8000-000000000001"
U2 = "01903f5a-0000-7000-8000-000000000002"


@pytest.fixture(autouse=True)
def clean_cache():
    clear_resolution_cache()
    yield
    clear_resolution_cache()


@pytest.mark.asyncio
async def test_a_miss_without_fallback_does_not_stop_the_chunk(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json={"results": [
        {"status": "not_found"}, {"status": "not_found"}, {"status": "found", "entry_id": U1},
    ]})
    httpx_mock.add_response(url=CANDIDATES_URL, json={"results": [{"status": "not_found"}]})
    httpx_mock.add_response(url=CANDIDATES_URL, json={"results": [{
        "status": "found", "total": 1, "candidates": [{"entry_id": U2}],
    }]})

    result = await resolve_entity_ids(
        ["GONE", "VIA-VALUE", "GOOD"], "template", "wip", bypass_cache=True, strict=False,
    )
    assert result == {"VIA-VALUE": U2, "GOOD": U1}
    assert _get_cached(_entity_key("wip", "template", "GOOD", None)) == U1
    assert _get_cached(_entity_key("wip", "template", "VIA-VALUE", None)) == U2
    assert _get_cached(_entity_key("wip", "template", "GONE", None)) is None
    calls = httpx_mock.get_requests()
    assert [str(r.url) for r in calls] == [RESOLVE_URL, CANDIDATES_URL, CANDIDATES_URL]
    assert len(json.loads(calls[0].content)) == 3


@pytest.mark.asyncio
async def test_a_term_miss_gets_no_fallback(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json={"results": [
        {"status": "not_found"}, {"status": "found", "entry_id": U1},
    ]})
    result = await resolve_entity_ids(
        ["ns:T:gone", "ns:T:good"], "term", "wip", bypass_cache=True, strict=False,
    )
    assert result == {"ns:T:good": U1}
    assert [str(r.url) for r in httpx_mock.get_requests()] == [RESOLVE_URL]
