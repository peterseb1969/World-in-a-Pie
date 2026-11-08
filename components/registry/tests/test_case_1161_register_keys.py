"""Register's branches the suite did not reach, and its totals over a mixed
batch, pinned before register_keys' refactor.

Provenance: CASE-1161. An empty batch answers an empty response; an entry_id
the caller provides that already exists is refused as already_exists naming
where it lives (a restore needs a clean target); and the response's created
/ already_exists / errors counts match the per-item statuses of a batch that
holds every outcome.
"""

import uuid

import pytest
from httpx import AsyncClient

BASE = "/api/registry"
REGISTER = f"{BASE}/entries/register"


def _tag() -> str:
    return uuid.uuid4().hex[:10]


async def _register(client: AsyncClient, headers: dict, items: list[dict]) -> dict:
    r = await client.post(REGISTER, json=items, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _item(tag: str, **extra) -> dict:
    return {"namespace": "default", "entity_type": "documents", "composite_key": {"k": tag}, **extra}


@pytest.mark.asyncio
async def test_an_empty_batch_answers_an_empty_response(client, auth_headers):
    body = await _register(client, auth_headers, [])
    assert {k: body[k] for k in ("results", "total", "created", "already_exists", "errors")} == {
        "results": [], "total": 0, "created": 0, "already_exists": 0, "errors": 0,
    }


@pytest.mark.asyncio
async def test_a_provided_entry_id_that_exists_is_refused(client, auth_headers):
    entry_id = str(uuid.uuid4())
    first = await _register(client, auth_headers, [_item(_tag(), entry_id=entry_id)])
    assert first["results"][0]["status"] == "created"
    assert first["results"][0]["registry_id"] == entry_id

    again = await _register(client, auth_headers, [_item(_tag(), entry_id=entry_id)])
    [result] = again["results"]
    assert result["status"] == "error"
    assert result["error_code"] == "already_exists"
    assert result["error"] == (
        f"entry_id '{entry_id}' already exists (namespace='default', entity_type='documents'). "
        "Restore mode requires a clean target — delete existing data first."
    )
    assert (again["created"], again["already_exists"], again["errors"]) == (0, 0, 1)


@pytest.mark.asyncio
async def test_the_totals_match_the_per_item_statuses_of_a_mixed_batch(client, auth_headers):
    held, new, dup = _tag(), _tag(), _tag()
    await _register(client, auth_headers, [_item(held)])
    body = await _register(client, auth_headers, [
        _item(new),                                   # created
        _item(held),                                  # already_exists
        {**_item(_tag()), "entity_type": "bogus"},    # error: invalid entity type
        _item(dup),                                   # created
        _item(dup),                                   # duplicate of the one before
        _item(new),                                   # duplicate of the first
    ])
    statuses = [r["status"] for r in body["results"]]
    assert statuses == ["created", "already_exists", "error", "created", "already_exists", "already_exists"]
    assert body["results"][2]["details"] == {"rule": "invalid_entity_type"}
    assert body["results"][4]["registry_id"] == body["results"][3]["registry_id"]
    assert body["results"][5]["registry_id"] == body["results"][0]["registry_id"]
    assert (body["total"], body["created"], body["already_exists"], body["errors"]) == (6, 2, 3, 1)
