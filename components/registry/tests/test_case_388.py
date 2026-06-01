"""Tests for CASE-388 — scoped api-key sees its own namespace in the
canonical `GET /api/registry/namespaces` list.

The bug (filed 2026-05-14, CASE-373 Phase 1 smoke): a scoped api-key with
`namespaces=["wip"]` got HTTP 200 + `{"items": []}` from the canonical list —
its own namespace was filtered out, even though it has `read` via the
CASE-351 namespace-list fallback.

These tests reproduce the smoke against the real registry app (a runtime
scoped key authenticating through `require_api_key`, exactly the smoke's
path) and assert the acceptance criteria:
  - scoped key sees its in-scope namespace,
  - does NOT see an out-of-scope namespace,
  - the master/admin key still sees both.

CASE-388's `_resolve_permission` fallback (grants.py) + the per-row filter in
`list_namespaces` + the api_key provider populating `raw_claims["namespaces"]`
from the stored key together make this work; the test pins it as a regression
guard.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

NS_API = "/api/registry/namespaces"
KEY_API = "/api/registry/api-keys"


async def _scoped_key(client: AsyncClient, auth_headers: dict, name: str, namespaces: list[str]) -> str:
    """Create a non-admin api-key scoped to `namespaces`; return its plaintext."""
    resp = await client.post(
        KEY_API,
        json={"name": name, "namespaces": namespaces},  # no groups → non-admin
        headers=auth_headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["plaintext_key"]


def _prefixes(list_resp) -> set[str]:
    assert list_resp.status_code == 200, list_resp.text
    return {item["prefix"] for item in list_resp.json()}


@pytest.mark.asyncio
async def test_scoped_key_sees_its_namespace_in_canonical_list(
    client: AsyncClient, auth_headers: dict
):
    """The primary CASE-388 repro: a key scoped to 'case388-mine' sees that
    namespace in GET /api/registry/namespaces, and does NOT see an
    out-of-scope one."""
    for prefix in ("case388-mine", "case388-other"):
        await client.post(NS_API, json={"prefix": prefix}, headers=auth_headers)

    key = await _scoped_key(client, auth_headers, "case388-scoped", ["case388-mine"])

    visible = _prefixes(await client.get(NS_API, headers={"X-API-Key": key}))
    assert "case388-mine" in visible, "scoped key must see its in-scope namespace"
    assert "case388-other" not in visible, "out-of-scope namespace must not leak"


@pytest.mark.asyncio
async def test_master_key_sees_all_namespaces(
    client: AsyncClient, auth_headers: dict
):
    """Regression protection: the admin/master key still sees both."""
    for prefix in ("case388-admin-a", "case388-admin-b"):
        await client.post(NS_API, json={"prefix": prefix}, headers=auth_headers)

    visible = _prefixes(await client.get(NS_API, headers=auth_headers))
    assert {"case388-admin-a", "case388-admin-b"} <= visible


@pytest.mark.asyncio
async def test_multi_namespace_scoped_key_sees_each(
    client: AsyncClient, auth_headers: dict
):
    """A key scoped to two namespaces sees both, and nothing it isn't scoped to."""
    for prefix in ("case388-m1", "case388-m2", "case388-m3"):
        await client.post(NS_API, json={"prefix": prefix}, headers=auth_headers)

    key = await _scoped_key(
        client, auth_headers, "case388-multi", ["case388-m1", "case388-m2"]
    )

    visible = _prefixes(await client.get(NS_API, headers={"X-API-Key": key}))
    assert {"case388-m1", "case388-m2"} <= visible
    assert "case388-m3" not in visible
