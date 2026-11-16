"""A key created now may not name itself with '/'.

A key is read, changed and revoked at /api-keys/{name}, one URL path
segment, and a percent-encoded '/' is decoded back to '/' before routing:
a key named with one could be created and then never addressed by name.
Creation refuses it, as def-store refuses '/' in a terminology value. Only
creation: a key already stored, loaded from a key file or restored under
such a name keeps working (key_name_error, the rule those paths read, does
not change).
"""

from __future__ import annotations

import uuid
from urllib.parse import quote

import pytest

from registry.models.api_key import key_name_error, new_key_name_error


def _name(stem: str) -> str:
    return f"{stem}-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_creating_a_key_named_with_a_slash_is_refused(client, auth_headers):
    r = await client.post("/api/registry/api-keys", headers=auth_headers, json={
        "name": _name("ci/2"), "namespaces": ["default"],
    })
    assert r.status_code == 422, r.text
    assert "contains '/'" in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stem", ["ci#2", "ci?2", "ci%2"])
async def test_a_key_named_with_hash_query_or_percent_is_addressed_by_its_whole_name(
    client, auth_headers, stem,
):
    # Encoded, such a name is one segment: the key is read and revoked as
    # itself, never as the key its prefix spells.
    name = _name(stem)
    r = await client.post("/api/registry/api-keys", headers=auth_headers, json={
        "name": name, "namespaces": ["default"],
    })
    assert r.status_code == 201, r.text
    got = await client.get(f"/api/registry/api-keys/{quote(name, safe='')}", headers=auth_headers)
    assert got.status_code == 200 and got.json()["name"] == name
    gone = await client.delete(f"/api/registry/api-keys/{quote(name, safe='')}", headers=auth_headers)
    assert gone.status_code in (200, 204), gone.text


def test_only_creation_refuses_the_slash():
    assert new_key_name_error("team/ci") is not None
    assert key_name_error("team/ci") is None
    assert new_key_name_error("apikey:ci") == key_name_error("apikey:ci")
    assert new_key_name_error("ci#2") is None
