"""The Registry's admin gate actually runs. Provenance: CASE-844.

`require_admin_key` was an uncalled alias of the `require_admin` FACTORY:
FastAPI called the zero-arg factory, took the returned closure as the
dependency's value, and the group check never executed — every
authenticated caller passed all 15 admin endpoints, up to and including
minting itself a wip-admins API key.

These tests pin the contract from both sides with real credentials
through the real provider chain: a runtime key with no groups must get
403 on every admin route, and the admin (legacy/master) key must pass
the gate on each. The 403 assertions also permanently reject the silent
factory-uncalled shape — re-alias the factory and all 15 fail at once.
"""

import uuid

import pytest
from httpx import AsyncClient

# (method, path, body) for every admin-gated route. Bodies are minimal —
# the gate runs before the handler, so a 403 must appear regardless of
# payload validity or resource existence.
ADMIN_ROUTES = [
    ("POST", "/api/registry/namespaces", {"prefix": "case844", "description": "x"}),
    ("PUT", "/api/registry/namespaces/case844", {"description": "x"}),
    ("POST", "/api/registry/namespaces/case844/archive", None),
    ("POST", "/api/registry/namespaces/case844/restore", None),
    ("POST", "/api/registry/namespaces/initialize-wip", None),
    ("POST", "/api/registry/namespaces/case844/export", None),
    ("POST", "/api/registry/namespaces/import", None),
    ("DELETE", "/api/registry/namespaces/case844", None),
    ("POST", "/api/registry/namespaces/case844/resume-delete", None),
    ("PATCH", "/api/registry/namespaces/case844", {"deletion_mode": "retain"}),
    ("POST", "/api/registry/api-keys", {"name": "escalated", "groups": ["wip-admins"]}),
    ("GET", "/api/registry/api-keys", None),
    ("GET", "/api/registry/api-keys/some-key", None),
    ("PATCH", "/api/registry/api-keys/some-key", {"description": "x"}),
    ("DELETE", "/api/registry/api-keys/some-key", None),
]


async def _mint_non_admin_key(client: AsyncClient, auth_headers: dict) -> dict:
    """A real runtime key with no groups and a single-namespace scope —
    the exact caller shape the original escalation used."""
    resp = await client.post(
        "/api/registry/api-keys",
        headers=auth_headers,
        # Unique per mint: the provider keeps runtime keys in process
        # memory across tests, so a fixed name collides on re-mint.
        json={
            "name": f"nonadmin-844-{uuid.uuid4().hex[:8]}",
            "groups": [],
            "namespaces": ["default"],
        },
    )
    assert resp.status_code == 201, resp.text
    return {"X-API-Key": resp.json()["plaintext_key"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
async def test_non_admin_key_gets_403(
    client: AsyncClient, auth_headers: dict, method: str, path: str, body
):
    non_admin = await _mint_non_admin_key(client, auth_headers)
    resp = await client.request(method, path, headers=non_admin, json=body)
    assert resp.status_code == 403, (
        f"{method} {path} answered {resp.status_code} to a non-admin key — "
        f"the admin gate did not run: {resp.text}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
async def test_admin_key_passes_the_gate(
    client: AsyncClient, auth_headers: dict, method: str, path: str, body
):
    """The admin key must never be rejected BY THE GATE — any status but
    403 is acceptable here (404/409/422 for missing resources or minimal
    bodies are the handler's business, not the gate's)."""
    resp = await client.request(method, path, headers=auth_headers, json=body)
    assert resp.status_code != 403, (
        f"{method} {path} rejected the admin key with 403: {resp.text}"
    )


@pytest.mark.asyncio
async def test_escalation_is_closed(client: AsyncClient, auth_headers: dict):
    """The concrete attack from the filing: a namespace-scoped groupless
    key must not be able to mint itself a wip-admins key."""
    non_admin = await _mint_non_admin_key(client, auth_headers)
    resp = await client.post(
        "/api/registry/api-keys",
        headers=non_admin,
        json={"name": "superadmin-844", "groups": ["wip-admins"]},
    )
    assert resp.status_code == 403
    # And the admin key never saw such a key created.
    listing = await client.get("/api/registry/api-keys", headers=auth_headers)
    names = [k["name"] for k in listing.json()["items"]]
    assert "superadmin-844" not in names
