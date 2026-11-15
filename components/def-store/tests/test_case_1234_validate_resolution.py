"""/validate and /validate/bulk resolve the terminology through the Registry.

The terminology identifier (canonical id, value, ns:VALUE or a Registry
synonym) resolves in the request's ``namespace`` query parameter. The
by-value search def-store ran against its own collection, across every
readable namespace, is gone with the ``terminology_value`` field, so a
synonym resolves here as everywhere else, and an identifier the Registry
finds ambiguous is answered as such, with the candidates' message. Every
refusal is in the response shape, never an HTTP error status.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient

from registry.main import app as registry_app

API = "/api/def-store"


async def _terminology(client, headers, value: str, *terms: str) -> str:
    r = await client.post(f"{API}/terminologies", headers=headers, json=[{
        "value": value, "label": value, "namespace": "wip",
    }])
    tid = r.json()["results"][0]["id"]
    if terms:
        r = await client.post(f"{API}/terminologies/{tid}/terms", headers=headers,
                              json=[{"value": t} for t in terms])
        assert r.json()["failed"] == 0, r.text
    return tid


async def _add_synonym(entry_id: str, key: dict) -> None:
    async with AsyncClient(transport=ASGITransport(app=registry_app), base_url="http://registry") as r:
        resp = await r.post(
            "/api/registry/synonyms/add",
            headers={"X-API-Key": os.environ["MASTER_API_KEY"]},
            json=[{
                "target_id": entry_id, "synonym_namespace": "wip",
                "synonym_entity_type": "terminologies", "synonym_composite_key": key,
            }],
        )
    assert resp.json()["results"][0]["status"] == "added", resp.text


@pytest.mark.asyncio
async def test_a_registry_synonym_of_the_terminology_resolves(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "SYN_1234", "alpha")
    await _add_synonym(tid, {"vendor_vocab": "VENDOR-1234"})

    single = await client.post(f"{API}/validate", headers=auth_headers, params={"namespace": "wip"},
                               json={"terminology_id": "VENDOR-1234", "value": "alpha"})
    bulk = await client.post(f"{API}/validate/bulk", headers=auth_headers, params={"namespace": "wip"},
                             json={"items": [{"terminology_id": "VENDOR-1234", "value": "alpha"}]})

    assert single.status_code == 200
    assert single.json()["valid"] is True and single.json()["terminology_id"] == tid, single.json()
    (item,) = bulk.json()["results"]
    assert item["valid"] is True and item["terminology_id"] == tid, item


@pytest.mark.asyncio
async def test_an_identifier_naming_two_terminologies_is_ambiguous(client: AsyncClient, auth_headers: dict):
    # Two terminologies carry the value, each as a synonym under its own
    # qualifier: the Registry cannot tell which one the bare value means.
    one = await _terminology(client, auth_headers, "AMB_1234_ONE")
    other = await _terminology(client, auth_headers, "AMB_1234_OTHER")
    await _add_synonym(one, {"vendor_a": "AMB_1234"})
    await _add_synonym(other, {"vendor_b": "AMB_1234"})

    single = await client.post(f"{API}/validate", headers=auth_headers, params={"namespace": "wip"},
                               json={"terminology_id": "AMB_1234", "value": "x"})
    bulk = await client.post(f"{API}/validate/bulk", headers=auth_headers, params={"namespace": "wip"},
                             json={"items": [{"terminology_id": "AMB_1234", "value": "x"}]})

    assert single.status_code == 200, single.text
    assert single.json()["error_code"] == "ambiguous_terminology", single.json()
    assert bulk.json()["results"][0]["error_code"] == "ambiguous_terminology", bulk.json()


@pytest.mark.asyncio
async def test_an_unknown_identifier_is_not_found_in_the_response_shape(
    client: AsyncClient, auth_headers: dict,
):
    # Resolution in a namespace 404s on a miss; the door keeps answering
    # 200 with error_code not_found, as it did for an id it passed through.
    for ident in ("NO_SUCH_1234", "019a0000-0000-7000-8000-000000001234"):
        r = await client.post(f"{API}/validate", headers=auth_headers, params={"namespace": "wip"},
                              json={"terminology_id": ident, "value": "x"})
        assert r.status_code == 200, r.text
        assert r.json()["valid"] is False and r.json()["error_code"] == "not_found", r.json()
