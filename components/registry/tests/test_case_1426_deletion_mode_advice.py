"""A refusal about deletion_mode names a call that works as written.

Provenance: CASE-1426. Two routes set a namespace's deletion_mode and take it
in different places: PUT /namespaces/{prefix} reads it from the JSON body, the
narrow PATCH route from query parameters. The delete refusal said only "via
PATCH", and PATCH's own confirm refusal did not say where the flag goes, so a
caller who sent PUT's body shape to PATCH met a bare 422 and read the route as
broken. Each test here follows the advice exactly as the refusal words it.
"""

from __future__ import annotations

import json
import re

import pytest
from httpx import AsyncClient


async def _retain_namespace(client: AsyncClient, headers: dict, prefix: str) -> str:
    await client.post("/api/registry/namespaces", json={"prefix": prefix}, headers=headers)
    resp = await client.delete(f"/api/registry/namespaces/{prefix}", headers=headers)
    assert resp.status_code == 400
    return resp.json()["detail"]


@pytest.mark.asyncio
async def test_the_put_the_delete_refusal_names_enables_the_delete(
    client: AsyncClient, auth_headers: dict,
):
    detail = await _retain_namespace(client, auth_headers, "adv-put")
    match = re.search(r"PUT (/api/registry/namespaces/adv-put) with body (\{.*?\})", detail)
    assert match, detail

    resp = await client.put(match.group(1), json=json.loads(match.group(2)), headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["deletion_mode"] == "full"

    resp = await client.delete("/api/registry/namespaces/adv-put", headers=auth_headers)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_the_patch_the_delete_refusal_names_enables_the_delete(
    client: AsyncClient, auth_headers: dict,
):
    detail = await _retain_namespace(client, auth_headers, "adv-patch")
    match = re.search(r"PATCH (/api/registry/namespaces/adv-patch\?\S+?)\.?$", detail)
    assert match, detail

    resp = await client.patch(match.group(1), headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["deletion_mode"] == "full"

    resp = await client.delete("/api/registry/namespaces/adv-patch", headers=auth_headers)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_patch_says_its_confirm_flag_is_a_query_parameter(
    client: AsyncClient, auth_headers: dict,
):
    await client.post("/api/registry/namespaces", json={"prefix": "adv-confirm"}, headers=auth_headers)
    resp = await client.patch(
        "/api/registry/namespaces/adv-confirm",
        params={"deletion_mode": "full"},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "query parameter confirm_enable_deletion=true" in resp.json()["detail"]
