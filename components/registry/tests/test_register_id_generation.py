"""Register mints new ids from the namespace config it already read.

Each new entry's id used to come from IdGeneratorService.generate, which
reads the namespace again, one database round trip per new entry. In a
500-document bulk write that was about 100 ms of the Registry's ~260 ms.
Register now reads the namespace once per request and mints every id from
that config. The response also reports its time per phase.
"""

import uuid

import pytest
from httpx import AsyncClient

from registry.services.id_generator import IdGeneratorService

REGISTER = "/api/registry/entries/register"


@pytest.fixture
def no_per_item_namespace_read(monkeypatch):
    async def must_not_run(*args, **kwargs):
        raise AssertionError("register must not read the namespace once per item")

    monkeypatch.setattr(IdGeneratorService, "generate", must_not_run)


@pytest.mark.asyncio
async def test_a_batch_mints_its_ids_without_reading_the_namespace_per_item(
    client: AsyncClient, auth_headers: dict, no_per_item_namespace_read,
):
    tag = uuid.uuid4().hex[:8]
    items = [
        {"namespace": "default", "entity_type": "terms", "composite_key": {"idgen": tag, "n": i}}
        for i in range(30)
    ]
    r = await client.post(REGISTER, json=items, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 30 and body["errors"] == 0
    ids = [x["registry_id"] for x in body["results"]]
    assert len(set(ids)) == 30

    again = (await client.post(REGISTER, json=items, headers=auth_headers)).json()
    assert again["already_exists"] == 30
    assert [x["registry_id"] for x in again["results"]] == ids


@pytest.mark.asyncio
async def test_a_prefixed_namespace_still_mints_its_sequence(
    client: AsyncClient, auth_headers: dict, no_per_item_namespace_read,
):
    prefix = f"idg-{uuid.uuid4().hex[:6]}"
    id_prefix = f"IDG{uuid.uuid4().hex[:4].upper()}-"
    r = await client.post("/api/registry/namespaces", headers=auth_headers, json={
        "prefix": prefix,
        "id_config": {"terms": {"algorithm": "prefixed", "prefix": id_prefix, "pad": 6}},
    })
    assert r.status_code == 200, r.text

    items = [
        {"namespace": prefix, "entity_type": "terms", "composite_key": {"n": i}}
        for i in range(3)
    ]
    body = (await client.post(REGISTER, json=items, headers=auth_headers)).json()
    assert [x["registry_id"] for x in body["results"]] == [
        f"{id_prefix}000001", f"{id_prefix}000002", f"{id_prefix}000003",
    ]


@pytest.mark.asyncio
async def test_an_unknown_namespace_is_refused_per_item(
    client: AsyncClient, auth_headers: dict, no_per_item_namespace_read,
):
    items = [{"namespace": "no-such-ns", "entity_type": "terms", "composite_key": {"n": 1}}]
    body = (await client.post(REGISTER, json=items, headers=auth_headers)).json()
    assert body["results"][0]["status"] == "error"
    assert body["results"][0]["error_code"] == "not_found"


@pytest.mark.asyncio
async def test_the_response_reports_its_phases(client: AsyncClient, auth_headers: dict):
    items = [{"namespace": "default", "entity_type": "terms",
              "composite_key": {"idgen-timing": uuid.uuid4().hex}}]
    timing = (await client.post(REGISTER, json=items, headers=auth_headers)).json()["timing"]
    for phase in ("0_namespace", "2_find_existing", "3_build_entries", "3b_claim_keys",
                  "4_insert_entries", "total"):
        assert phase in timing, timing
