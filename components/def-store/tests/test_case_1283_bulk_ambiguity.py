"""The def-store bulk write doors answer an ambiguous term key as ambiguous.

Provenance: CASE-1283 (review of its first fix).

A term-relation endpoint, or a deprecation's replacement, named by a value
several terms bear was passed on unresolved, and the item was answered
"not found". Each such item is now held out of the write and answered per
item with error_code ambiguous_reference and the candidates; the other
items of the request are written as before, still HTTP 200.

The Registry here is the real in-process one, which holds no ambiguous
key, so the term resolver is made to answer "ambiguous" for one value.
"""

import pytest
from httpx import AsyncClient

from wip_auth import fastapi_helpers
from wip_auth.resolve import AmbiguousEntityError

API = "/api/def-store"
AMBIGUOUS = "crimson"


@pytest.fixture
def ambiguous_value(monkeypatch):
    real = fastapi_helpers.resolve_term_by_fields
    candidates = [
        {"entry_id": "T-1", "namespace": "wip", "matched_via": "synonym"},
        {"entry_id": "T-2", "namespace": "wip", "matched_via": "synonym"},
    ]

    async def resolve(value, terminology, namespace, *, bypass_cache=False):
        if value == AMBIGUOUS:
            raise AmbiguousEntityError(value, "term", candidates, 2)
        return await real(value, terminology, namespace, bypass_cache=bypass_cache)

    monkeypatch.setattr(fastapi_helpers, "resolve_term_by_fields", resolve)
    return candidates


async def _world(client: AsyncClient, auth_headers: dict) -> dict[str, str]:
    resp = await client.post(
        f"{API}/terminologies",
        json=[{"value": "COLORS", "label": "Colors", "namespace": "wip"}],
        headers=auth_headers,
    )
    terminology_id = resp.json()["results"][0]["id"]
    resp = await client.post(
        f"{API}/terminologies/{terminology_id}/terms",
        json=[{"value": v, "label": v} for v in ("red", "blue", "green")],
        headers=auth_headers,
    )
    rows = resp.json()["results"]
    assert all(r["status"] == "created" for r in rows), rows
    return {r["value"]: r["id"] for r in rows}


@pytest.mark.asyncio
async def test_a_relation_with_an_ambiguous_endpoint_is_answered_as_ambiguous(
    client: AsyncClient, auth_headers: dict, ambiguous_value,
):
    terms = await _world(client, auth_headers)

    def relation(source: str) -> dict:
        return {
            "source_term_id": source, "source_terminology": "COLORS",
            "target_term_id": "blue", "target_terminology": "COLORS",
            "relation_type": "related_to",
        }

    resp = await client.post(
        f"{API}/ontology/term-relations",
        json=[relation(AMBIGUOUS), relation("red")],
        headers=auth_headers,
        params={"namespace": "wip"},
    )

    assert resp.status_code == 200
    body = resp.json()
    first, second = body["results"]
    assert (first["index"], first["status"], first["error_code"]) == (
        0, "error", "ambiguous_reference",
    ), first
    assert "ambiguous" in first["error"]
    assert first["details"]["field"] == "source_term_id"
    assert first["details"]["candidates"] == ambiguous_value
    assert (second["index"], second["status"]) == (1, "created"), second
    assert (body["total"], body["succeeded"], body["failed"]) == (2, 1, 1)

    resp = await client.get(
        f"{API}/ontology/term-relations",
        headers=auth_headers,
        params={"namespace": "wip", "term_id": terms["red"]},
    )
    assert [r["target_term_id"] for r in resp.json()["items"]] == [terms["blue"]]


@pytest.mark.asyncio
async def test_a_deprecation_with_an_ambiguous_replacement_writes_nothing(
    client: AsyncClient, auth_headers: dict, ambiguous_value,
):
    terms = await _world(client, auth_headers)

    resp = await client.post(
        f"{API}/terms/deprecate",
        json=[{"term_id": "red", "reason": "merged", "replaced_by_term_id": AMBIGUOUS}],
        headers=auth_headers,
        params={"namespace": "wip", "terminology": "COLORS"},
    )

    assert resp.status_code == 200
    (row,) = resp.json()["results"]
    assert (row["status"], row["error_code"]) == ("error", "ambiguous_reference"), row
    assert row["details"]["field"] == "replaced_by_term_id"

    resp = await client.get(f"{API}/terms/{terms['red']}", headers=auth_headers)
    assert resp.json()["status"] == "active"
