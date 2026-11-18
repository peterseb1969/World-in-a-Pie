"""Def-Store's 409 for a terminology identifier that names several
terminologies is an answer about the reference, not a failure to fetch it.

Def-Store answers 409 (error_code ambiguous_reference, the candidates in its
message) when a bare value resolves to several terminologies. The client
raised it as "Failed to get terminology: 409", so validation reported the
reference as unverifiable and an uncaught one became a 503 "Def-Store did not
answer". It is now ambiguous_reference, naming the candidates.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient

from template_store.services import def_store_client as dsc

API = "/api/template-store/templates"
MESSAGE = (
    "terminology identifier 'SHADE' is ambiguous: it resolves to 2 entities "
    "(T1 via {'vendor_a': 'SHADE'}, T2 via {'vendor_b': 'SHADE'})"
)


def _ambiguous(request: httpx.Request) -> httpx.Response:
    return httpx.Response(409, json={"detail": {
        "error": "ambiguous", "error_code": "ambiguous_reference", "message": MESSAGE,
    }})


@pytest.mark.asyncio
async def test_the_client_reads_a_409_as_ambiguous():
    real = httpx.AsyncClient

    def mocked(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(_ambiguous), **kwargs)

    client = dsc.DefStoreClient(base_url="http://def-store", api_key="k")
    with patch.object(dsc.httpx, "AsyncClient", mocked), pytest.raises(dsc.DefStoreAmbiguousError) as caught:
        await client.terminology_exists("SHADE", namespace="wip")
    assert str(caught.value) == MESSAGE


@pytest.mark.asyncio
async def test_validate_reports_an_ambiguous_terminology_as_such(client: AsyncClient, auth_headers):
    resp = await client.post(API, headers=auth_headers, json=[{
        "namespace": "wip", "value": "AMBIG_TERM_REF", "label": "Ambig", "fields": [
            {"name": "id", "type": "string", "label": "Id"},
            {"name": "g", "type": "term", "label": "G", "terminology_ref": "GENDER"},
        ],
    }])
    item = resp.json()["results"][0]
    ambiguous = AsyncMock()
    ambiguous.terminology_exists = AsyncMock(side_effect=dsc.DefStoreAmbiguousError(MESSAGE))
    with patch("template_store.services.template_service.get_def_store_client",
               return_value=ambiguous):
        resp = await client.post(f"{API}/{item['id']}/validate", headers=auth_headers,
                                 params={"namespace": "wip"}, json={})
    body = resp.json()
    assert body["valid"] is False, body
    assert [e["code"] for e in body["errors"]] == ["ambiguous_reference"], body
    assert "T1" in body["errors"][0]["message"] and "T2" in body["errors"][0]["message"]


# CASE-1430: def-store answers the service key, which sees every namespace.
# Candidates the writer may not reference must not reach it: none left reads
# as a terminology that does not exist, and a partial list names only the
# visible ones.
HIDDEN = [{"entry_id": "T-HIDDEN-1", "namespace": "hidden"}, {"entry_id": "T-HIDDEN-2", "namespace": "hidden"}]
SEEN = {"entry_id": "T-SEEN", "namespace": "wip"}


async def _hidden_refused(self, namespace, entity_id):  # type: ignore[no-untyped-def]
    return namespace == "hidden"


async def _validate_with(client: AsyncClient, auth_headers, def_store) -> dict:  # type: ignore[no-untyped-def]
    resp = await client.post(API, headers=auth_headers, json=[{
        "namespace": "wip", "value": "AMBIG_HIDDEN_REF", "label": "Ambig", "fields": [
            {"name": "id", "type": "string", "label": "Id"},
            {"name": "g", "type": "term", "label": "G", "terminology_ref": "GENDER"},
        ],
    }])
    item = resp.json()["results"][0]
    with patch("template_store.services.template_service.get_def_store_client", return_value=def_store), \
         patch("template_store.services.inbound_gate.InboundGate.refuses", _hidden_refused):
        resp = await client.post(f"{API}/{item['id']}/validate", headers=auth_headers,
                                 params={"namespace": "wip"}, json={})
    return resp.json()


def _answering(**kwargs) -> AsyncMock:  # type: ignore[no-untyped-def]
    def_store = AsyncMock()
    def_store.terminology_exists = AsyncMock(**kwargs)
    return def_store


@pytest.mark.asyncio
async def test_an_ambiguity_among_hidden_terminologies_reads_as_missing(client: AsyncClient, auth_headers):
    hidden = await _validate_with(client, auth_headers, _answering(
        side_effect=dsc.DefStoreAmbiguousError(MESSAGE, HIDDEN)))
    unknown = await _validate_with(client, auth_headers, _answering(return_value=False))
    assert hidden["errors"] == unknown["errors"], (hidden, unknown)
    assert [e["code"] for e in hidden["errors"]] == ["invalid_reference"]


@pytest.mark.asyncio
async def test_an_ambiguity_names_only_the_terminologies_the_writer_may_see(client: AsyncClient, auth_headers):
    body = await _validate_with(client, auth_headers, _answering(
        side_effect=dsc.DefStoreAmbiguousError(MESSAGE, [*HIDDEN, SEEN])))
    [error] = body["errors"]
    assert error["code"] == "ambiguous_reference"
    assert "T-SEEN" in error["message"] and "T-HIDDEN" not in error["message"]


@pytest.mark.asyncio
async def test_the_client_keeps_the_candidates_of_the_409():
    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": {
            "error": "ambiguous", "error_code": "ambiguous_reference",
            "message": MESSAGE, "candidates": [*HIDDEN, SEEN],
        }})

    real = httpx.AsyncClient

    def mocked(*args, **kwargs):  # type: ignore[no-untyped-def]
        return real(*args, transport=httpx.MockTransport(answer), **kwargs)

    client = dsc.DefStoreClient(base_url="http://def-store", api_key="k")
    with patch.object(dsc.httpx, "AsyncClient", mocked), pytest.raises(dsc.DefStoreAmbiguousError) as caught:
        await client.terminology_exists("SHADE", namespace="wip")
    assert caught.value.candidates == [*HIDDEN, SEEN]
