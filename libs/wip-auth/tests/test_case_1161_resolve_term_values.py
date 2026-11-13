"""Batch term resolution, pinned before resolve_term_values' refactor.

Provenance: CASE-1161. The suite never reached these branches: an empty
request asks nothing, and one terminology ref repeated across items is
resolved through the Registry once. Also pinned: a mixed request keeps item
order — an unresolved terminology's error, a repeated value asked once, each
result stamped with its terminology id.
"""

from unittest.mock import AsyncMock, patch

import pytest

from wip_auth import term_resolution
from wip_auth.term_resolution import resolve_term_values

from .test_term_resolution import UUID_A, _asked, _fake_def_store


@pytest.fixture(autouse=True)
def _def_store_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The key term resolution presents to Def-Store. There is no stand-in
    default: without one every resolution refuses, naming the variable."""
    monkeypatch.setenv("DEF_STORE_API_KEY", "test-def-store-key")



@pytest.mark.asyncio
async def test_an_empty_request_asks_nothing():
    fetch = _fake_def_store()
    with patch.object(term_resolution, "_resolve_remote", fetch), \
         patch.object(term_resolution, "resolve_entity_id", new_callable=AsyncMock) as resolve:
        assert await resolve_term_values([], "ns-a") == []
    fetch.assert_not_awaited()
    resolve.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_repeated_ref_resolves_once_and_results_keep_item_order():
    fetch = _fake_def_store()

    async def resolve(ref, entity_type, namespace):
        return UUID_A if ref == "STATUS" else None

    with patch.object(term_resolution, "_resolve_remote", fetch), \
         patch.object(term_resolution, "resolve_entity_id", new=AsyncMock(side_effect=resolve)) as reg:
        results = await resolve_term_values([
            {"terminology_ref": "STATUS", "value": "ns-a-only"},
            {"terminology_ref": "MISSING", "value": "x"},
            {"terminology_ref": "STATUS", "value": "nope"},
            {"terminology_ref": "STATUS", "value": "ns-a-only"},
        ], "ns-a")

    assert [c.args[0] for c in reg.await_args_list] == ["STATUS", "MISSING"]
    assert _asked(fetch) == [(UUID_A, "ns-a-only"), (UUID_A, "nope")]
    assert [r.get("valid") for r in results] == [True, False, False, True]
    assert results[1] == {"valid": False, "error": "Terminology 'MISSING' not found"}
    assert [r.get("terminology_id") for r in results] == [UUID_A, None, UUID_A, UUID_A]
    assert results[0] == results[3] and results[0] is not results[3]
