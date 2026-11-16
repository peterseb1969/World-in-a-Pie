"""A term whose value contains '/' is readable by that value through every door.

Term values may carry '/' (unit codes like mg/dL, path-like codes). The
routes that take a term identifier in the URL path matched one path segment,
so `/terms/A/B` (raw, or with the '/' sent as %2F, which the server decodes
before routing) matched no route and answered 404, and `trail/` was
redirected to another term. These routes take the identifier as a path
converter now. Run through the real app and the in-process Registry, because
routing is the defect.
"""

from urllib.parse import quote

import pytest
from httpx import AsyncClient

API = "/api/def-store"
TERMINOLOGY = "SLASH_UNITS"
ROOT = "A/B"
CHILDREN = ["mg/dL", "/lead", "trail/", "a//b"]
NS = {"namespace": "wip"}


def _enc(value: str) -> str:
    return quote(value, safe="")


async def _world(client: AsyncClient, auth_headers: dict) -> dict[str, str]:
    resp = await client.post(
        f"{API}/terminologies",
        json=[{"value": TERMINOLOGY, "label": TERMINOLOGY, "namespace": "wip"}],
        headers=auth_headers,
    )
    tid = resp.json()["results"][0]["id"]
    resp = await client.post(
        f"{API}/terminologies/{tid}/terms",
        json=[{"value": v, "label": v} for v in [ROOT, *CHILDREN]],
        headers=auth_headers,
    )
    results = resp.json()["results"]
    assert all(r["status"] == "created" for r in results), results
    # Bulk results come back in submission order.
    ids = {v: r["id"] for v, r in zip([ROOT, *CHILDREN], results, strict=True)}
    resp = await client.post(
        f"{API}/ontology/term-relations",
        json=[
            {"source_term_id": ids[v], "target_term_id": ids[ROOT], "relation_type": "is_a"}
            for v in CHILDREN
        ],
        headers=auth_headers,
        params=NS,
    )
    assert resp.json()["succeeded"] == len(CHILDREN), resp.text
    return ids


@pytest.mark.asyncio
async def test_a_slash_value_is_read_back_by_value(client: AsyncClient, auth_headers: dict):
    ids = await _world(client, auth_headers)
    field = {**NS, "terminology": TERMINOLOGY}
    for value, term_id in ids.items():
        doors = {
            "field form, encoded": (f"{API}/terms/{_enc(value)}", field),
            "field form, raw": (f"{API}/terms/{value}", field),
            "qualified form": (f"{API}/terms/{_enc(f'wip:{TERMINOLOGY}:{value}')}", NS),
        }
        for door, (url, params) in doors.items():
            resp = await client.get(url, headers=auth_headers, params=params)
            assert resp.status_code == 200, (value, door, resp.status_code, resp.text)
            assert resp.json()["term_id"] == term_id, (value, door)


@pytest.mark.asyncio
async def test_the_ontology_doors_take_a_slash_value(client: AsyncClient, auth_headers: dict):
    ids = await _world(client, auth_headers)
    field = {**NS, "terminology": TERMINOLOGY}
    for value in CHILDREN:
        resp = await client.get(
            f"{API}/ontology/terms/{_enc(value)}/parents", headers=auth_headers, params=field,
        )
        assert resp.status_code == 200, (value, resp.text)
        assert [r["target_term_id"] for r in resp.json()] == [ids[ROOT]], value

        resp = await client.get(
            f"{API}/ontology/terms/{_enc(value)}/ancestors", headers=auth_headers, params=field,
        )
        assert resp.status_code == 200, (value, resp.text)
        assert resp.json()["term_id"] == ids[value]
        assert ids[ROOT] in {n["term_id"] for n in resp.json()["nodes"]}

    resp = await client.get(
        f"{API}/ontology/terms/{_enc(ROOT)}/children", headers=auth_headers, params=field,
    )
    assert resp.status_code == 200, resp.text
    assert {r["source_term_id"] for r in resp.json()} == {ids[v] for v in CHILDREN}

    resp = await client.get(
        f"{API}/ontology/terms/{_enc(ROOT)}/descendants", headers=auth_headers, params=field,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["term_id"] == ids[ROOT]
    assert {n["term_id"] for n in resp.json()["nodes"]} == {ids[v] for v in CHILDREN}


@pytest.mark.asyncio
async def test_the_audit_door_takes_a_slash_value(client: AsyncClient, auth_headers: dict):
    ids = await _world(client, auth_headers)
    for value, term_id in ids.items():
        resp = await client.get(
            f"{API}/audit/terms/{_enc(value)}",
            headers=auth_headers,
            params={**NS, "terminology": TERMINOLOGY},
        )
        assert resp.status_code == 200, (value, resp.text)
        assert resp.json()["total"] >= 1, value
        assert {e["term_id"] for e in resp.json()["items"]} == {term_id}, value
