"""sort_by / sort_order on the terminology and term list endpoints.

The sort field and direction are enum-typed query parameters, so an unknown
field or direction is a 422 at the door — never an unhandled ValueError
surfacing as a 500 from the service layer.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

API = "/api/def-store"


async def _mk_terminology(client, auth_headers, value, label):
    resp = await client.post(
        f"{API}/terminologies", headers=auth_headers,
        json=[{"namespace": "wip", "value": value, "label": label}],
    )
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] in ("created", "unchanged"), item
    return item["id"]


@pytest.mark.asyncio
async def test_terminologies_sort_by_value_desc(client: AsyncClient, auth_headers: dict):
    await _mk_terminology(client, auth_headers, "SORT_AAA", "Zeta")
    await _mk_terminology(client, auth_headers, "SORT_ZZZ", "Alpha")
    resp = await client.get(
        f"{API}/terminologies", headers=auth_headers,
        params={"namespace": "wip", "sort_by": "value", "sort_order": "desc", "page_size": 100},
    )
    assert resp.status_code == 200, resp.text
    values = [t["value"] for t in resp.json()["items"] if t["value"].startswith("SORT_")]
    assert values == ["SORT_ZZZ", "SORT_AAA"]

    resp = await client.get(
        f"{API}/terminologies", headers=auth_headers,
        params={"namespace": "wip", "sort_by": "label", "page_size": 100},
    )
    labels = [t["label"] for t in resp.json()["items"] if t["value"].startswith("SORT_")]
    assert labels == ["Alpha", "Zeta"]  # default direction asc


@pytest.mark.asyncio
async def test_invalid_sort_params_are_422_not_500(client: AsyncClient, auth_headers: dict):
    resp = await client.get(
        f"{API}/terminologies", headers=auth_headers,
        params={"namespace": "wip", "sort_by": "no_such_field"},
    )
    assert resp.status_code == 422, resp.text
    resp = await client.get(
        f"{API}/terminologies", headers=auth_headers,
        params={"namespace": "wip", "sort_order": "sideways"},
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_terms_sort_and_invalid_params(client: AsyncClient, auth_headers: dict):
    tid = await _mk_terminology(client, auth_headers, "SORT_TERMS", "Sort terms")
    resp = await client.post(
        f"{API}/terminologies/{tid}/terms", headers=auth_headers,
        json=[
            {"value": "b_term", "label": "B", "sort_order": 2},
            {"value": "a_term", "label": "A", "sort_order": 1},
        ],
    )
    assert resp.status_code == 200, resp.text
    resp = await client.get(
        f"{API}/terminologies/{tid}/terms", headers=auth_headers,
        params={"namespace": "wip", "sort_by": "value", "sort_order": "desc"},
    )
    assert resp.status_code == 200, resp.text
    assert [t["value"] for t in resp.json()["items"]] == ["b_term", "a_term"]
    resp = await client.get(
        f"{API}/terminologies/{tid}/terms", headers=auth_headers,
        params={"namespace": "wip", "sort_by": "nope"},
    )
    assert resp.status_code == 422, resp.text
