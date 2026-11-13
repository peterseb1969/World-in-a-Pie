"""The candidate lookup reads the caller's namespaces once, and checks a lock again.

POST /entries/lookup/candidates read the caller's readable namespaces once
per item, unbounded by the batch size; it is read once per request now. And
a namespace locked after that list was read still had its entries returned
and counted, where the sibling lookups check each match's namespace at
decision time; the lookup now asks again for the namespaces its matches come
from before it counts them.
"""

import uuid

import pytest

import registry.api.grants as grants

from .test_case_1226_lookup_candidates_locked import CANDIDATES, _lock, _namespace, _register


@pytest.mark.asyncio
async def test_the_readable_namespaces_are_read_once_per_request(client, auth_headers, monkeypatch):
    real = grants._unlocked_namespaces
    calls = []

    async def counted():
        calls.append(1)
        return await real()

    monkeypatch.setattr(grants, "_unlocked_namespaces", counted)
    items = [{"value": f"v-{uuid.uuid4().hex[:8]}"} for _ in range(25)]
    r = await client.post(CANDIDATES, json=items, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_namespace_locked_after_the_list_was_read_is_not_counted(
    client, auth_headers, monkeypatch,
):
    open_ns, locked_ns = await _namespace(client, auth_headers), await _namespace(client, auth_headers)
    value = f"v1226r-{uuid.uuid4().hex[:8]}"
    open_id = await _register(client, auth_headers, open_ns, value)
    locked_id = await _register(client, auth_headers, locked_ns, value)
    # The list was read before the lock: it still names the locked namespace.
    stale = await grants._unlocked_namespaces()

    async def read_before_the_lock():
        return stale

    monkeypatch.setattr(grants, "_unlocked_namespaces", read_before_the_lock)
    await _lock(locked_ns)

    r = await client.post(CANDIDATES, json=[{"value": value}, {"value": locked_id}], headers=auth_headers)
    assert r.status_code == 200, r.text
    by_value, by_id = sorted(r.json()["results"], key=lambda x: x["index"])
    assert by_value["total"] == 1
    assert [c["entry_id"] for c in by_value["candidates"]] == [open_id]
    assert by_id["status"] == "not_found"
