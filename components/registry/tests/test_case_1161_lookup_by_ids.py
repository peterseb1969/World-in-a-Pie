"""By-id lookup branches the suite did not reach, pinned before
lookup_by_ids' refactor.

Provenance: CASE-1161. An item that raises is an error of its own, and the
four counts match the statuses: one request yields each status, so a count
taken from the wrong status fails. (The source-data fetch this file also
pinned was removed by CASE-1187; test_case_1187.py pins that it is refused.)
"""

import uuid

import pytest

from registry.api import entries as entries_api
from tests.test_case_1128_merge import _register

LOOKUP = "/api/registry/entries/lookup/by-id"


@pytest.mark.asyncio
async def test_an_item_that_raises_is_an_error_and_the_counts_follow(client, auth_headers, monkeypatch):
    shared = f"L1161-{uuid.uuid4().hex[:8]}"
    # Two entries whose keys carry one value: a lookup of it is ambiguous.
    await _register(client, auth_headers, {"value": shared, "side": "a"})
    await _register(client, auth_headers, {"value": shared, "side": "b"})
    one = await _register(client, auth_headers, {"value": f"{shared}-one"})

    real = entries_api.invalid_entity_type_fields

    def boom(entity_type):
        if entity_type == "documents":
            raise RuntimeError("item failed")
        return real(entity_type)

    monkeypatch.setattr(entries_api, "invalid_entity_type_fields", boom)
    r = await client.post(LOOKUP, headers=auth_headers, json=[
        {"entry_id": "nothing-1161", "entity_type": "documents"},
        {"entry_id": "nothing-1161"},
        {"entry_id": "x", "entity_type": "bogus"},
        {"entry_id": shared},
        {"entry_id": one},
    ])
    body = r.json()
    assert [x["status"] for x in body["results"]] == [
        "error", "not_found", "error", "ambiguous", "found",
    ]
    assert "item failed" in body["results"][0]["error"]
    assert (body["found"], body["not_found"], body["ambiguous"], body["errors"]) == (1, 1, 1, 2)
