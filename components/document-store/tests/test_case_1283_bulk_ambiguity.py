"""The document create door answers an ambiguous template value as ambiguous.

Provenance: CASE-1283 (review of its first fix).

A template value several templates bear was passed on unresolved, and the
item was answered "template not found". It is now held out of the write and
answered per item with error_code ambiguous_reference and the candidates;
the other items are written as before (HTTP 200). With
continue_on_error=false the request stops there, as at any failing item.

The Registry here is the real in-process one, which holds no ambiguous key,
so the batch resolver is made to answer "ambiguous" for one value.
"""

import pytest
from httpx import AsyncClient

from wip_auth import fastapi_helpers
from wip_auth.resolve import AmbiguousEntityError

AMBIGUOUS = "TWIN"
CANDIDATES = [{"entry_id": "T-1", "matched_via": "synonym"}, {"entry_id": "T-2", "matched_via": "synonym"}]


@pytest.fixture
def ambiguous_template(monkeypatch):
    real = fastapi_helpers.resolve_entity_ids

    async def resolve(raw_ids, entity_type, namespace, *args, ambiguous=None, **kwargs):
        rest = [raw_id for raw_id in raw_ids if raw_id != AMBIGUOUS]
        if ambiguous is not None:
            kwargs["ambiguous"] = ambiguous
        result = await real(rest, entity_type, namespace, *args, **kwargs)
        if AMBIGUOUS in raw_ids and ambiguous is not None:
            ambiguous[AMBIGUOUS] = AmbiguousEntityError(AMBIGUOUS, entity_type, CANDIDATES, 2)
        return result

    monkeypatch.setattr(fastapi_helpers, "resolve_entity_ids", resolve)


@pytest.mark.asyncio
async def test_an_ambiguous_template_is_answered_per_item(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict, ambiguous_template,
):
    resp = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        json=[
            {"namespace": "wip", "template_id": "PERSON", "data": sample_person_data},
            {"namespace": "wip", "template_id": AMBIGUOUS, "data": sample_person_data},
        ],
    )

    assert resp.status_code == 200
    body = resp.json()
    first, second = body["results"]
    assert (first["index"], first["status"]) == (0, "created"), first
    assert (second["index"], second["status"], second["error_code"]) == (
        1, "error", "ambiguous_reference",
    ), second
    assert second["details"]["field"] == "template_id"
    assert second["details"]["candidates"] == CANDIDATES
    assert (body["total"], body["succeeded"], body["failed"]) == (2, 1, 1)


@pytest.mark.asyncio
async def test_a_request_that_stops_at_its_first_failure_stops_there(
    client: AsyncClient, auth_headers: dict, sample_person_data: dict, ambiguous_template,
):
    resp = await client.post(
        "/api/document-store/documents",
        headers=auth_headers,
        params={"continue_on_error": "false"},
        json=[
            {"namespace": "wip", "template_id": AMBIGUOUS, "data": sample_person_data},
            {"namespace": "wip", "template_id": "PERSON", "data": sample_person_data},
        ],
    )

    assert resp.status_code == 200
    body = resp.json()
    assert [(r["status"], r["error_code"]) for r in body["results"]] == [
        ("error", "ambiguous_reference"), ("skipped", None),
    ]
    assert (body["total"], body["succeeded"], body["failed"]) == (2, 0, 1)
