"""A namespace whose id algorithm only validates IDs answers per item, never 500.

The "pattern" and "any" algorithms check an ID a caller supplies; WIP mints
none for them. A register item without an entry_id, or a provision, in such a
namespace reached the generator, which raised a ValueError no handler caught:
the whole batch answered 500. The mint check now refuses these algorithms
before anything is minted, so register answers that item with an error (and
the batch's other items normally) and provision answers 422. An item that
supplies its own entry_id is unaffected (CASE-1271, finding 1781c704 R1).
"""

import uuid

import pytest

BASE = "/api/registry"


async def _namespace(client, auth_headers, algorithm: dict) -> str:
    prefix = f"voc-{uuid.uuid4().hex[:8]}"
    r = await client.post(f"{BASE}/namespaces", headers=auth_headers, json={
        "prefix": prefix, "id_config": {"terms": algorithm},
    })
    assert r.status_code in (200, 201), r.text
    return prefix


@pytest.mark.asyncio
@pytest.mark.parametrize("algorithm", [
    {"algorithm": "any"},
    {"algorithm": "pattern", "pattern": "T-[0-9]+"},
])
async def test_register_without_an_id_is_refused_per_item(client, auth_headers, algorithm):
    prefix = await _namespace(client, auth_headers, algorithm)
    r = await client.post(f"{BASE}/entries/register", headers=auth_headers, json=[
        {"namespace": prefix, "entity_type": "terms", "composite_key": {"value": "minted"}},
        {"namespace": prefix, "entity_type": "terms", "composite_key": {"value": "supplied"},
         "entry_id": "T-42"},
        {"namespace": prefix, "entity_type": "templates", "composite_key": {"value": "uuid7"}},
    ])
    assert r.status_code == 200, r.text
    minted, supplied, other = r.json()["results"]
    assert minted["status"] == "error", minted
    assert "validation-only" in minted["error"] and algorithm["algorithm"] in minted["error"]
    assert supplied["status"] == "created" and supplied["registry_id"] == "T-42", supplied
    assert other["status"] == "created", other


@pytest.mark.asyncio
async def test_provision_is_refused_with_422(client, auth_headers):
    prefix = await _namespace(client, auth_headers, {"algorithm": "any"})
    r = await client.post(f"{BASE}/entries/provision", headers=auth_headers, json={
        "namespace": prefix, "entity_type": "terms", "defer_claim": True,
    })
    assert r.status_code == 422, r.text
    assert "validation-only" in r.json()["detail"]
