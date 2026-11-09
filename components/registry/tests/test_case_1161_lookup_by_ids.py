"""By-id lookup branches the suite did not reach, pinned before
lookup_by_ids' refactor.

Provenance: CASE-1161. An item that raises is an error of its own, and the
counts match the statuses. (The source-data fetch this file also pinned was
removed by CASE-1187; test_case_1187.py pins that it is refused.)
"""

import pytest

from registry.api import entries as entries_api

LOOKUP = "/api/registry/entries/lookup/by-id"


@pytest.mark.asyncio
async def test_an_item_that_raises_is_an_error_and_the_counts_follow(client, auth_headers, monkeypatch):
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
    ])
    body = r.json()
    assert [x["status"] for x in body["results"]] == ["error", "not_found", "error"]
    assert "item failed" in body["results"][0]["error"]
    assert (body["found"], body["not_found"], body["ambiguous"], body["errors"]) == (0, 1, 0, 2)
