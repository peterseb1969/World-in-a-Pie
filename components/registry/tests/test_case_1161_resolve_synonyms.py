"""Resolve's request error, pinned before resolve_synonyms' refactor.

Provenance: CASE-1161. The suite never reached this branch: an item with
neither entry_id nor composite_key is an error of its own, and the counts
follow the statuses.
"""

import pytest


@pytest.mark.asyncio
async def test_an_item_with_neither_id_nor_key_is_an_error(client, auth_headers):
    r = await client.post("/api/registry/entries/resolve", headers=auth_headers, json=[
        {"namespace": "default"},
        {"entry_id": "no-such-1161"},
    ])
    assert r.status_code == 200, r.text
    body = r.json()
    first = body["results"][0]
    assert (first["status"], first["error_code"], first["error"]) == (
        "error", "invalid_request", "Provide either entry_id or composite_key",
    )
    assert body["results"][1]["status"] == "not_found"
    assert (body["found"], body["not_found"], body["ambiguous"], body["errors"]) == (0, 1, 0, 1)
