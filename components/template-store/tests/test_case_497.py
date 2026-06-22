"""CASE-497 — namespace-scopable by-value-and-version lookup + a by-template_id
versions route (the deferred backend half of CASE-496).

A template `value` is unique only within a namespace; a `template_id` is global
and stable across versions.
"""

import pytest
from httpx import AsyncClient

FIELDS = [{"name": "code", "label": "Code", "type": "string", "mandatory": True}]


async def _create(client: AsyncClient, auth_headers: dict, namespace: str, value: str, fields=None) -> str:
    resp = await client.post(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[{
            "namespace": namespace, "value": value, "label": value,
            "identity_fields": ["code"], "fields": fields or FIELDS,
        }],
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["succeeded"] == 1, data
    return data["results"][0]["id"]


# --- Item 1: namespace on by-value-and-version --------------------------------

@pytest.mark.asyncio
async def test_by_value_and_version_scoped_by_namespace(client: AsyncClient, auth_headers: dict):
    # same value legitimately exists in two namespaces
    await _create(client, auth_headers, "wip", "CASE497DUP")
    await _create(client, auth_headers, "test-ns", "CASE497DUP")

    r_wip = await client.get(
        "/api/template-store/templates/by-value/CASE497DUP/versions/1?namespace=wip",
        headers=auth_headers,
    )
    assert r_wip.status_code == 200, r_wip.text
    assert r_wip.json()["namespace"] == "wip"

    r_ns = await client.get(
        "/api/template-store/templates/by-value/CASE497DUP/versions/1?namespace=test-ns",
        headers=auth_headers,
    )
    assert r_ns.status_code == 200, r_ns.text
    assert r_ns.json()["namespace"] == "test-ns"


@pytest.mark.asyncio
async def test_by_value_and_version_unscoped_still_works(client: AsyncClient, auth_headers: dict):
    """Backward compatible: omitting namespace still resolves (value unique here)."""
    await _create(client, auth_headers, "wip", "CASE497SOLO")
    r = await client.get(
        "/api/template-store/templates/by-value/CASE497SOLO/versions/1",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == "CASE497SOLO"


# --- Item 2: by-template_id versions route ------------------------------------

@pytest.mark.asyncio
async def test_versions_by_template_id(client: AsyncClient, auth_headers: dict):
    tid = await _create(client, auth_headers, "wip", "CASE497VER")
    # bump to v2 via PUT (add an optional field)
    put = await client.put(
        "/api/template-store/templates",
        headers=auth_headers,
        json=[{
            "template_id": tid, "value": "CASE497VER", "label": "CASE497VER",
            "identity_fields": ["code"],
            "fields": [*FIELDS, {"name": "extra", "label": "Extra", "type": "string"}],
        }],
    )
    assert put.status_code == 200, put.text
    assert put.json()["succeeded"] == 1, put.text

    r = await client.get(f"/api/template-store/templates/{tid}/versions", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2, body
    assert {v["version"] for v in body["items"]} == {1, 2}
    assert all(v["template_id"] == tid for v in body["items"])  # all share the id


@pytest.mark.asyncio
async def test_versions_by_template_id_not_found(client: AsyncClient, auth_headers: dict):
    r = await client.get(
        "/api/template-store/templates/019eee00-0000-7000-8000-000000000000/versions",
        headers=auth_headers,
    )
    assert r.status_code == 404
