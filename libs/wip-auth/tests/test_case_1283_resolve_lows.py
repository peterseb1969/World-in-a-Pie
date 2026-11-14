"""Three resolve answers that were wrong in a corner.

Provenance: CASE-1283.

- An invalidation handled while a resolve was in flight was undone: the
  resolve then cached the answer the invalidation had dropped, and served it
  until its TTL ran out.
- The field-form term door took an ambiguous answer for a miss (the error is
  a subclass of the not-found one) and tried its next candidate, which could
  resolve to another term.
- An ambiguous answer the Registry cut short (it lists at most a fixed number
  of candidates) was reported as an exact count.
"""

import httpx
import pytest

from wip_auth.resolve import (
    AmbiguousEntityError,
    clear_resolution_cache,
    forget_resolutions,
    resolve_entity_id,
    resolve_entity_ids,
    resolve_term_by_fields,
)

RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"
ONE = "019a0000-0000-7000-8000-000000000001"
TWO = "019a0000-0000-7000-8000-000000000002"
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


def _found(entry_id: str) -> httpx.Response:
    return httpx.Response(200, json={"results": [{"status": "found", "entry_id": entry_id}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["single", "batch"])
async def test_an_invalidation_during_a_resolve_is_not_undone(httpx_mock, door):
    calls = []

    def answer(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            # The entity's invalidation is handled while this answer is in
            # flight, before the resolve caches it.
            forget_resolutions([ONE])
        return _found(ONE)

    httpx_mock.add_callback(answer, url=RESOLVE_URL, is_reusable=True)

    async def resolve() -> str:
        if door == "single":
            return await resolve_entity_id("COLOR", "template", "paint")
        return (await resolve_entity_ids(["COLOR"], "template", "paint"))["COLOR"]

    assert await resolve() == ONE
    assert await resolve() == ONE
    assert len(calls) == 2, "the invalidated answer was cached"
    # With no invalidation in flight the answer is cached as before.
    assert await resolve() == ONE
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_the_field_form_door_refuses_an_ambiguous_key(httpx_mock):
    calls = []

    def answer(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            # ns-qualified terminology 'paint:COLORS': several entries hold it.
            return httpx.Response(200, json={"results": [
                {"status": "ambiguous", "entry_id": None, "candidates": CANDIDATES},
            ]})
        # The literal value 'paint:COLORS' in namespace 'paint': one term.
        return _found(TWO)

    httpx_mock.add_callback(answer, url=RESOLVE_URL, is_reusable=True, is_optional=True)
    with pytest.raises(AmbiguousEntityError):
        await resolve_term_by_fields("red", "paint:COLORS", "paint")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_cut_short_ambiguous_answer_says_so(httpx_mock):
    httpx_mock.add_callback(
        lambda _r: httpx.Response(200, json={"results": [{
            "status": "ambiguous", "entry_id": None, "candidates": CANDIDATES,
            "details": {"truncated": True},
        }]}),
        url=RESOLVE_URL,
    )
    with pytest.raises(AmbiguousEntityError) as info:
        await resolve_entity_id("COLOR", "template", "paint")
    assert info.value.truncated
    assert "resolves to 2 or more entities" in str(info.value)


@pytest.mark.asyncio
async def test_a_complete_ambiguous_answer_states_its_count(httpx_mock):
    httpx_mock.add_callback(
        lambda _r: httpx.Response(200, json={"results": [
            {"status": "ambiguous", "entry_id": None, "candidates": CANDIDATES},
        ]}),
        url=RESOLVE_URL,
    )
    with pytest.raises(AmbiguousEntityError) as info:
        await resolve_entity_id("COLOR", "template", "paint")
    assert not info.value.truncated
    assert "resolves to 2 entities" in str(info.value)
