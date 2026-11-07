"""A key the Registry finds under several entities is refused, never picked.

The Registry's /resolve answers `ambiguous`, with the candidates, when more
than one entity the caller can read holds a composite key. Treated as a miss,
the single door fell back to the value lookup and could resolve to one of
them anyway, and a non-strict batch did the same. Provenance: CASE-1126.
"""

import httpx
import pytest

from wip_auth.resolve import (
    AmbiguousEntityError,
    clear_resolution_cache,
    resolve_entity_id,
    resolve_entity_ids,
)

RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"
CANDIDATES_URL = "http://localhost:8001/api/registry/entries/lookup/candidates"
ONE = "019a0000-0000-7000-8000-000000000001"
TWO = "019a0000-0000-7000-8000-000000000002"
CANDIDATES = [
    {"entry_id": ONE, "namespace": "paint", "entity_type": "templates", "matched_via": "synonym"},
    {"entry_id": TWO, "namespace": "glue", "entity_type": "templates", "matched_via": "synonym"},
]


@pytest.fixture(autouse=True)
def clean_cache():
    clear_resolution_cache()
    yield
    clear_resolution_cache()


def _ambiguous(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"results": [
        {"status": "ambiguous", "entry_id": None, "candidates": CANDIDATES},
    ]})


def _one_candidate(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"results": [
        {"status": "found", "total": 1, "candidates": [CANDIDATES[0]]},
    ]})


@pytest.mark.asyncio
async def test_the_single_door_raises_and_does_not_fall_back(httpx_mock):
    httpx_mock.add_callback(_ambiguous, url=RESOLVE_URL)
    # Were the value lookup asked, it would answer with one entity.
    httpx_mock.add_callback(_one_candidate, url=CANDIDATES_URL, is_optional=True)
    with pytest.raises(AmbiguousEntityError) as info:
        await resolve_entity_id("COLOR", "template", "paint")
    assert [c["entry_id"] for c in info.value.candidates] == [ONE, TWO]
    assert info.value.total == 2


@pytest.mark.asyncio
async def test_a_strict_batch_raises(httpx_mock):
    httpx_mock.add_callback(_ambiguous, url=RESOLVE_URL)
    with pytest.raises(AmbiguousEntityError):
        await resolve_entity_ids(["COLOR"], "template", "paint")


@pytest.mark.asyncio
async def test_a_non_strict_batch_leaves_it_unresolved(httpx_mock):
    httpx_mock.add_callback(_ambiguous, url=RESOLVE_URL)
    httpx_mock.add_callback(_one_candidate, url=CANDIDATES_URL, is_optional=True)
    assert await resolve_entity_ids(["COLOR"], "template", "paint", strict=False) == {}
