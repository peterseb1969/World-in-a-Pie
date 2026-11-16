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
