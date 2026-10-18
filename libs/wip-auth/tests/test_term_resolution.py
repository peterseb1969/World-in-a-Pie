"""wip_auth.term_resolution — the shared way to resolve term values.

Contract:

  - A canonical UUID terminology ref short-circuits: no Registry round-trip.
  - A value-form ref resolves through the Registry with the caller's
    namespace as context; def-store is then asked by canonical UUID. There is
    no unscoped by-value fallback.
  - A value-form ref with no namespace context, or one the Registry cannot
    resolve, reports terminology-not-found — never an arbitrary pick.
  - The same bare value ref in two namespaces reaches two vocabularies.
  - Inside term_resolution_batch, a (terminology UUID, value) pair is asked
    once — also when documents resolve concurrently — keyed by the canonical
    UUID, so sharing never crosses namespaces; nothing outlives the batch.
  - def-store's answer for a contested name (matches > 1) surfaces as
    `ambiguous` + every bearer as a candidate.
  - A def-store failure raises TermResolutionError.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from wip_auth import term_resolution
from wip_auth.resolve import EntityNotFoundError
from wip_auth.term_resolution import (
    TermResolutionError,
    resolve_term_values,
    shape_resolution,
    term_resolution_batch,
)

UUID_A = "019f0000-0000-7000-8000-00000000000a"
UUID_B = "019f0000-0000-7000-8000-00000000000b"
NS_OF = {UUID_A: "ns-a", UUID_B: "ns-b"}


def _fake_def_store():
    """def-store stand-in: each terminology accepts only '<its-ns>-only'."""
    async def resolve_remote(keys, *_args):
        await asyncio.sleep(0)  # yield, so concurrent callers really overlap
        return [
            {"valid": True, "matched_term": {"term_id": f"T-{NS_OF[tid]}", "value": value}, "matched_via": "value"}
            if value == f"{NS_OF[tid]}-only"
            else {"valid": False, "matched_term": None, "matched_via": None, "suggestion": None}
            for tid, value in keys
        ]
    return AsyncMock(side_effect=resolve_remote)


def _asked(fetch) -> list:
    """Every (terminology_uuid, value) pair sent to def-store, in order."""
    return [key for call in fetch.await_args_list for key in call.args[0]]


async def _resolve_by_ns(ref, entity_type, namespace):
    return UUID_A if namespace == "ns-a" else UUID_B


async def _one(ref, value, namespace=None):
    (result,) = await resolve_term_values([{"terminology_ref": ref, "value": value}], namespace)
    return result


@pytest.mark.asyncio
async def test_uuid_ref_short_circuits_registry():
    fetch = _fake_def_store()
    with patch.object(term_resolution, "_resolve_remote", fetch), \
         patch.object(term_resolution, "resolve_entity_id", new_callable=AsyncMock) as resolve:
        assert (await _one(UUID_A, "ns-a-only", "ns-a"))["valid"] is True
    resolve.assert_not_awaited()
    assert _asked(fetch) == [(UUID_A, "ns-a-only")]


@pytest.mark.asyncio
async def test_value_ref_resolves_in_the_callers_namespace():
    fetch = _fake_def_store()
    with patch.object(term_resolution, "_resolve_remote", fetch), \
         patch.object(term_resolution, "resolve_entity_id", new_callable=AsyncMock, return_value=UUID_A) as resolve:
        assert (await _one("STATUS", "ns-a-only", "ns-a"))["valid"] is True
    resolve.assert_awaited_once_with("STATUS", "terminology", "ns-a")
    assert _asked(fetch) == [(UUID_A, "ns-a-only")]


@pytest.mark.asyncio
async def test_value_ref_without_namespace_is_not_found():
    fetch = _fake_def_store()
    with patch.object(term_resolution, "_resolve_remote", fetch), \
         patch.object(term_resolution, "resolve_entity_id", new_callable=AsyncMock) as resolve:
        result = await _one("STATUS", "anything")
    assert result["valid"] is False and "not found" in result["error"]
    resolve.assert_not_awaited()
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_unresolvable_value_ref_is_not_found():
    fetch = _fake_def_store()
    with patch.object(term_resolution, "_resolve_remote", fetch), patch.object(
        term_resolution, "resolve_entity_id", new_callable=AsyncMock,
        side_effect=EntityNotFoundError("STATUS", "terminology"),
    ):
        result = await _one("STATUS", "anything", "ns-a")
    assert result["valid"] is False and "not found" in result["error"]
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolution_never_crosses_namespaces():
    fetch = _fake_def_store()
    with patch.object(term_resolution, "_resolve_remote", fetch), \
         patch.object(term_resolution, "resolve_entity_id", side_effect=_resolve_by_ns):
        assert (await _one("STATUS", "ns-a-only", "ns-a"))["valid"] is True
        assert (await _one("STATUS", "ns-b-only", "ns-b"))["valid"] is True
        assert (await _one("STATUS", "ns-a-only", "ns-b"))["valid"] is False
    assert {tid for tid, _ in _asked(fetch)} == {UUID_A, UUID_B}


@pytest.mark.asyncio
async def test_batch_resolves_a_repeated_value_once_and_never_across_namespaces():
    fetch = _fake_def_store()
    with patch.object(term_resolution, "_resolve_remote", fetch), \
         patch.object(term_resolution, "resolve_entity_id", side_effect=_resolve_by_ns), \
         term_resolution_batch():
        for _ in range(3):
            assert (await _one("STATUS", "ns-a-only", "ns-a"))["valid"]
        assert not (await _one("STATUS", "ns-a-only", "ns-b"))["valid"]
    assert _asked(fetch) == [(UUID_A, "ns-a-only"), (UUID_B, "ns-a-only")]


@pytest.mark.asyncio
async def test_batch_shares_answers_between_concurrent_callers():
    """Documents of one bulk write validate concurrently: the first asks,
    the rest await the same answer."""
    fetch = _fake_def_store()
    with patch.object(term_resolution, "_resolve_remote", fetch), term_resolution_batch():
        results = await asyncio.gather(*(_one(UUID_A, "ns-a-only", "ns-a") for _ in range(10)))
    assert all(r["valid"] for r in results)
    assert _asked(fetch) == [(UUID_A, "ns-a-only")]


@pytest.mark.asyncio
async def test_without_a_batch_every_call_asks_def_store():
    fetch = _fake_def_store()
    with patch.object(term_resolution, "_resolve_remote", fetch):
        for _ in range(2):
            await _one(UUID_A, "ns-a-only", "ns-a")
        with term_resolution_batch():
            await _one(UUID_A, "ns-a-only", "ns-a")
        await _one(UUID_A, "ns-a-only", "ns-a")
    assert len(_asked(fetch)) == 4


@pytest.mark.asyncio
async def test_failure_reaches_every_waiter_and_the_batch_can_retry():
    fetch = AsyncMock(side_effect=TermResolutionError("down"))
    with patch.object(term_resolution, "_resolve_remote", fetch), term_resolution_batch():
        with pytest.raises(TermResolutionError):
            await _one(UUID_A, "ns-a-only", "ns-a")
        with pytest.raises(TermResolutionError):  # asked again, not served a dead entry
            await _one(UUID_A, "ns-a-only", "ns-a")
    assert fetch.await_count == 2


def _term(term_id: str, value: str) -> dict:
    return {"term_id": term_id, "value": value, "label": value, "names": [], "status": "active"}


def test_contested_name_maps_to_ambiguous_with_every_bearer():
    result = shape_resolution({
        "valid": True, "matched_via": "alias", "matches": 2,
        "matched_term": _term("T-1", "RO4876646"),
        "candidates": [
            {"term": _term("T-1", "RO4876646"), "matched_name": {"value": "bevacizumab"}},
            {"term": _term("T-2", "RO4876646-000"), "matched_name": {"value": "bevacizumab"}},
        ],
    })
    assert result["matched_term"] == {"term_id": "T-1", "value": "RO4876646"}
    assert result["ambiguous"] is True
    assert [c["term_id"] for c in result["candidates"]] == ["T-1", "T-2"]


def test_single_bearer_or_value_match_is_not_ambiguous():
    for matched_via in ("value", "alias"):
        result = shape_resolution({"valid": True, "matched_via": matched_via, "matches": 1,
                                   "matched_term": _term("T-3", "avastin"), "candidates": []})
        assert result["valid"] is True and "ambiguous" not in result


def test_miss_carries_the_suggestion():
    result = shape_resolution({"valid": False, "matches": 0, "matched_term": None,
                               "suggestion": _term("T-1", "RO4876646")})
    assert result == {"valid": False, "matched_term": None, "matched_via": None,
                      "suggestion": {"term_id": "T-1", "value": "RO4876646"}}


@pytest.mark.asyncio
async def test_remote_call_shape_and_error(httpx_mock):
    base = "http://def-store.test:8002"
    httpx_mock.add_response(url=f"{base}/api/def-store/validate/bulk", json={"results": [
        {"valid": True, "matched_via": "value", "matches": 1, "matched_term": _term("T-1", "ADA")},
    ]})
    httpx_mock.add_response(url=f"{base}/api/def-store/validate/bulk", status_code=503, text="down")
    (ok,) = await resolve_term_values([{"terminology_ref": UUID_A, "value": "ada"}], base_url=base, api_key="k")
    assert ok["matched_term"]["term_id"] == "T-1"
    request = httpx_mock.get_requests()[0]
    assert request.headers["X-API-Key"] == "k"
    assert json.loads(request.content) == {"items": [{"terminology_id": UUID_A, "value": "ada"}]}
    with pytest.raises(TermResolutionError, match="503"):
        await resolve_term_values([{"terminology_ref": UUID_A, "value": "x"}], base_url=base, api_key="k")
