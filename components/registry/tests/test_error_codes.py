"""The Registry's error items carry a machine-readable error_code.

Reason statuses (already_exists, not_found, …) stay exactly as they are —
internal callers branch on them by value. Every item whose status is
"error" also names a code from the shared vocabulary, so no caller has to
match the message.
"""

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient

import registry
from wip_auth.testing import assert_error_items_carry_codes

API = "/api/registry"


def test_error_items_carry_codes():
    assert_error_items_carry_codes(Path(registry.__file__).parent)


async def _register(client: AsyncClient, headers: dict, **item) -> dict:
    resp = await client.post(f"{API}/entries/register", headers=headers, json=[item])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


@pytest.mark.asyncio
async def test_register_codes(client, auth_headers):
    item = await _register(client, auth_headers, namespace="default",
                           entity_type="no_such_type", composite_key={"k": "v"})
    assert (item["status"], item["error_code"]) == ("error", "invalid_request")
    assert item["details"] == {"rule": "invalid_entity_type"}

    item = await _register(client, auth_headers, namespace="no-such-namespace",
                           entity_type="terms", composite_key={"k": "v"})
    assert (item["status"], item["error_code"]) == ("error", "not_found")
    assert item["details"] == {"rule": "namespace"}


@pytest.mark.asyncio
async def test_reason_statuses_are_unchanged(client, auth_headers):
    """A duplicate register is a reason status, not an error item — the
    statuses callers branch on keep their meaning."""
    key = {"probe": uuid.uuid4().hex}
    first = await _register(client, auth_headers, namespace="default", entity_type="terms", composite_key=key)
    again = await _register(client, auth_headers, namespace="default", entity_type="terms", composite_key=key)
    assert first["status"] == "created"
    assert again["status"] == "already_exists"


@pytest.mark.asyncio
async def test_activate_inactive_entry_is_invalid_state(client, auth_headers):
    """An active entry answers the reason status already_active; an entry in
    any other non-reserved state (here: deactivated) is an error item."""
    created = await _register(client, auth_headers, namespace="default", entity_type="terms",
                              composite_key={"probe": uuid.uuid4().hex})
    resp = await client.request("DELETE", f"{API}/entries", headers=auth_headers,
                                json=[{"entry_id": created["registry_id"]}])
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"{API}/entries/activate", headers=auth_headers,
                             json=[{"entry_id": created["registry_id"]}])
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert (item["status"], item["error_code"]) == ("error", "invalid_state")
    assert item["details"] == {"rule": "not_reserved"}


@pytest.mark.asyncio
async def test_merge_with_itself_is_invalid_request(client, auth_headers):
    created = await _register(client, auth_headers, namespace="default", entity_type="documents",
                              composite_key={"probe": uuid.uuid4().hex})
    entry_id = created["registry_id"]
    resp = await client.post(f"{API}/synonyms/merge", headers=auth_headers,
                             json=[{"preferred_id": entry_id, "deprecated_id": entry_id}])
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert (item["status"], item["error_code"]) == ("error", "invalid_request")
    assert item["details"] == {"rule": "self_merge"}
