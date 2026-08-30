"""sort_by / sort_order on the template list endpoint — enum-typed at the
door, so a bad field or direction is a 422, never a service ValueError 500."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

API = "/api/template-store"


async def _mk(client, auth_headers, value, label):
    resp = await client.post(
        f"{API}/templates", headers=auth_headers,
        json=[{
            "namespace": "wip", "value": value, "label": label,
            "fields": [{"name": "id", "type": "string", "label": "Id", "mandatory": True}],
        }],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["status"] in ("created", "unchanged"), resp.text


@pytest.mark.asyncio
async def test_templates_sort_by_label_desc(client: AsyncClient, auth_headers: dict):
    await _mk(client, auth_headers, "SORT_T_A", "Zulu")
    await _mk(client, auth_headers, "SORT_T_B", "Alpha")
    resp = await client.get(
        f"{API}/templates", headers=auth_headers,
        params={"namespace": "wip", "sort_by": "label", "sort_order": "desc", "page_size": 100},
    )
    assert resp.status_code == 200, resp.text
    labels = [t["label"] for t in resp.json()["items"] if t["value"].startswith("SORT_T_")]
    assert labels == ["Zulu", "Alpha"]

    # latest_only takes the aggregation path — same contract.
    resp = await client.get(
        f"{API}/templates", headers=auth_headers,
        params={"namespace": "wip", "latest_only": "true", "sort_by": "label", "page_size": 100},
    )
    assert resp.status_code == 200, resp.text
    labels = [t["label"] for t in resp.json()["items"] if t["value"].startswith("SORT_T_")]
    assert labels == ["Alpha", "Zulu"]


@pytest.mark.asyncio
async def test_invalid_sort_params_are_422_not_500(client: AsyncClient, auth_headers: dict):
    for params in ({"sort_by": "no_such_field"}, {"sort_order": "sideways"}):
        resp = await client.get(
            f"{API}/templates", headers=auth_headers, params={"namespace": "wip", **params},
        )
        assert resp.status_code == 422, resp.text
