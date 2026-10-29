"""The bulk write door resolves in batches, not per item. Provenance: CASE-850.

resolve_bulk_ids used to await one Registry round-trip per item (two per
relation on the ontology door: a 200-relation request made 400 serial
resolve calls, each on a fresh connection). The door now batches the
string-form identifiers into one deduped resolve call per namespace,
keeps the field door per item, and preserves the old failure semantics:
a miss (or an unreachable Registry) leaves the item's id untouched for
the downstream BulkResponse — now with the single door's by-id fallback
on misses, so batch resolution is never weaker than single resolution.
"""

import json
from types import SimpleNamespace

import pytest

from wip_auth.fastapi_helpers import resolve_bulk_ids
from wip_auth.resolve import (
    _get_resolve_client,
    clear_resolution_cache,
    resolve_entity_ids,
)

RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"
CANDIDATES_URL = "http://localhost:8001/api/registry/entries/lookup/candidates"

U1 = "01903f5a-0000-7000-8000-000000000001"
U2 = "01903f5a-0000-7000-8000-000000000002"


@pytest.fixture(autouse=True)
def clean_cache():
    clear_resolution_cache()
    yield
    clear_resolution_cache()


def _found(*entry_ids: str) -> dict:
    return {"results": [{"status": "found", "entry_id": e} for e in entry_ids]}


@pytest.mark.asyncio
async def test_string_door_batches_and_dedupes(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json=_found(U1, U2, U1))

    items = [
        {"ref": "wip:ALPHA"}, {"ref": "wip:BETA"}, {"ref": "wip:ALPHA"},
        {"ref": "wip:GAMMA"}, {"ref": "wip:BETA"}, {"ref": "wip:ALPHA"},
    ]
    await resolve_bulk_ids(items, "ref", "template", "wip", bypass_cache=True)

    requests = httpx_mock.get_requests()
    resolve_calls = [r for r in requests if str(r.url) == RESOLVE_URL]
    assert len(resolve_calls) == 1, "expected ONE batched resolve call"
    payload = json.loads(resolve_calls[0].content)
    assert len(payload) == 3, "expected deduped payload (3 unique ids)"

    assert [i["ref"] for i in items] == [U1, U2, U1, U1, U2, U1]


@pytest.mark.asyncio
async def test_miss_passes_through_and_tries_the_value_fallback(httpx_mock):
    """A per-item miss keeps the old pass-through semantics, after trying
    the value fallback the single door has — batch must not be weaker."""
    httpx_mock.add_response(url=RESOLVE_URL, json={
        "results": [
            {"status": "found", "entry_id": U1},
            {"status": "not_found"},
        ]
    })
    httpx_mock.add_response(url=CANDIDATES_URL, json={"results": [{"status": "not_found"}]})

    items = [SimpleNamespace(ref="GOOD"), SimpleNamespace(ref="MISSING")]
    await resolve_bulk_ids(items, "ref", "template", "wip", bypass_cache=True)

    assert items[0].ref == U1
    assert items[1].ref == "MISSING"  # untouched — downstream reports it
    fallback_calls = [r for r in httpx_mock.get_requests() if str(r.url) == CANDIDATES_URL]
    assert len(fallback_calls) == 1


def _candidates(*entry_ids: str) -> dict:
    return {"results": [{
        "status": "found", "total": len(entry_ids),
        "candidates": [{"entry_id": e} for e in entry_ids],
    }]}


@pytest.mark.asyncio
async def test_value_fallback_can_rescue_a_miss(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json={"results": [{"status": "not_found"}]})
    httpx_mock.add_response(url=CANDIDATES_URL, json=_candidates(U2))

    result = await resolve_entity_ids(
        ["VALUE-FORM"], "document", "wip", bypass_cache=True, strict=False,
    )
    assert result == {"VALUE-FORM": U2}


@pytest.mark.asyncio
async def test_an_ambiguous_value_stays_unresolved_in_a_batch(httpx_mock):
    """CASE-975: the non-strict batch never picks one of several candidates."""
    httpx_mock.add_response(url=RESOLVE_URL, json={"results": [{"status": "not_found"}]})
    httpx_mock.add_response(url=CANDIDATES_URL, json=_candidates(U1, U2))

    result = await resolve_entity_ids(
        ["Low Water"], "document", "wip", bypass_cache=True, strict=False,
    )
    assert result == {}


@pytest.mark.asyncio
async def test_unreachable_registry_passes_all_items_through(httpx_mock):
    import httpx as _httpx

    httpx_mock.add_exception(_httpx.ConnectError("boom"))

    items = [{"ref": "A"}, {"ref": "B"}]
    # Must not raise — the old per-item door passed through on exactly this.
    await resolve_bulk_ids(items, "ref", "template", "wip", bypass_cache=True)
    assert [i["ref"] for i in items] == ["A", "B"]


@pytest.mark.asyncio
async def test_items_group_per_namespace(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json=_found(U1))
    httpx_mock.add_response(url=RESOLVE_URL, json=_found(U2))

    items = [
        {"ref": "ALPHA", "namespace": "ns-a"},
        {"ref": "ALPHA", "namespace": "ns-b"},
    ]
    await resolve_bulk_ids(items, "ref", "template", None, bypass_cache=True)

    resolve_calls = [r for r in httpx_mock.get_requests() if str(r.url) == RESOLVE_URL]
    assert len(resolve_calls) == 2, "one batched call per namespace"
    assert {items[0]["ref"], items[1]["ref"]} == {U1, U2}


@pytest.mark.asyncio
async def test_strict_batch_still_raises_on_miss(httpx_mock):
    from wip_auth.resolve import EntityNotFoundError

    httpx_mock.add_response(url=RESOLVE_URL, json={"results": [{"status": "not_found"}]})

    with pytest.raises(EntityNotFoundError):
        # Terms have no value fallback (so no CANDIDATES mock is registered);
        # the strict default raises as before.
        await resolve_entity_ids(["wip:T:missing"], "term", "wip", bypass_cache=True)


@pytest.mark.asyncio
async def test_chunking_splits_large_batches(httpx_mock):
    n = 450  # three chunks at _RESOLVE_CHUNK = 200
    ids = [f"V{i:04d}" for i in range(n)]
    for size in (200, 200, 50):
        httpx_mock.add_response(url=RESOLVE_URL, json=_found(*([U1] * size)))

    result = await resolve_entity_ids(
        ids, "template", "wip", bypass_cache=True, strict=False,
    )
    assert len(result) == n
    resolve_calls = [r for r in httpx_mock.get_requests() if str(r.url) == RESOLVE_URL]
    assert [len(json.loads(r.content)) for r in resolve_calls] == [200, 200, 50]


@pytest.mark.asyncio
async def test_pooled_client_is_reused_within_a_loop(httpx_mock):
    """The per-call fresh AsyncClient is gone: consecutive resolve work in
    one event loop shares one client (one connection pool)."""
    c1 = _get_resolve_client()
    c2 = _get_resolve_client()
    assert c1 is c2

    from wip_auth.resolve import set_resolve_transport

    try:
        set_resolve_transport(None)  # unchanged — still the same client
        assert _get_resolve_client() is c1
    finally:
        set_resolve_transport(None)
    # A transport change rebuilds (test setups inject per-suite transports).
    import httpx as _httpx

    try:
        set_resolve_transport(_httpx.MockTransport(lambda r: _httpx.Response(200)))
        assert _get_resolve_client() is not c1
    finally:
        set_resolve_transport(None)
