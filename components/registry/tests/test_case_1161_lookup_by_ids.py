"""By-id lookup branches the suite did not reach, pinned before
lookup_by_ids' refactor.

Provenance: CASE-1161. With fetch_source_data the lookup fetches the entry's
source_info.endpoint_url and returns its JSON as source_data, or an error
note when the fetch fails; an item that raises is an error of its own, and
the counts match the statuses.
"""

import uuid
from typing import ClassVar

import pytest

from registry.api import entries as entries_api

LOOKUP = "/api/registry/entries/lookup/by-id"


class _Response:
    def __init__(self, payload, fail):
        self._payload, self._fail = payload, fail

    def raise_for_status(self):
        if self._fail:
            raise RuntimeError("upstream 500")

    def json(self):
        return self._payload


class _Client:
    asked: ClassVar[list[str]] = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        _Client.asked.append(url)
        return _Response({"from": url}, fail="fail" in url)


async def _register(client, headers, tag: str, url: str) -> str:
    r = await client.post("/api/registry/entries/register", headers=headers, json=[{
        "namespace": "default", "entity_type": "terms", "composite_key": {"k": tag},
        "source_info": {"system_id": "src", "endpoint_url": url},
    }])
    assert r.status_code == 200, r.text
    return r.json()["results"][0]["registry_id"]


@pytest.mark.asyncio
async def test_source_data_is_fetched_or_the_failure_noted(client, auth_headers, monkeypatch):
    tag = uuid.uuid4().hex[:8]
    ok = await _register(client, auth_headers, f"ok{tag}", "https://src.example/ok")
    bad = await _register(client, auth_headers, f"bad{tag}", "https://src.example/fail")
    _Client.asked = []
    monkeypatch.setattr(entries_api.httpx, "AsyncClient", _Client)
    r = await client.post(LOOKUP, headers=auth_headers, json=[
        {"entry_id": ok, "fetch_source_data": True},
        {"entry_id": bad, "fetch_source_data": True},
        {"entry_id": ok},
    ])
    body = r.json()
    assert [x["status"] for x in body["results"]] == ["found", "found", "found"]
    assert body["results"][0]["source_data"] == {"from": "https://src.example/ok"}
    assert body["results"][1]["source_data"] == {"error": "Failed to fetch: upstream 500"}
    assert body["results"][2].get("source_data") is None
    assert _Client.asked == ["https://src.example/ok", "https://src.example/fail"]


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
