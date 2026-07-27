"""Strict term addressing on the def-store doors.

A term's identity is the tuple (namespace, terminology, value). The 2-part
'TERMINOLOGY:VALUE' colon shorthand is a lossy serialization of it — a
value that itself contains ':' (OBO ids like GO:0000278) is
indistinguishable from it, so the shorthand can silently resolve through a
decoy terminology (one literally named like the value's prefix). On a
write door that mutates or deletes the wrong entity; on a read door it
feeds a silently wrong answer. Every term door therefore rejects the
2-part form with 422 and accepts only the unambiguous forms: canonical
UUID, fully qualified 'ns:terminology:value', or the field form
(terminology scope + opaque value).

Contract under test, end-to-end against the real in-process Registry:

  - All five term write doors and all seven term read doors 422 on a
    2-part identifier — even when a decoy terminology exists that the
    old parse would have wrong-hit.
  - The field form (terminology= query param on term routes; per-item
    source_terminology/target_terminology on relation routes) addresses
    colon-carrying values opaquely and lands on the right term, with the
    decoy present.
  - The 3-part qualified form still works on writes.
  - The audit-trail read gets the same field door as every other read.
"""

import pytest
from httpx import AsyncClient

API = "/api/def-store"

REAL_VALUE = "GO:0000278"          # colon-carrying term value (OBO id shape)
REAL_VALUE_2 = "GO:0000279"        # replacement target for deprecation
DECOY_VALUE = "0000278"            # what a 2-part parse of REAL_VALUE would hit


async def _create_terminology(client, auth_headers, value):
    resp = await client.post(
        f"{API}/terminologies",
        json=[{"value": value, "label": value, "namespace": "wip"}],
        headers=auth_headers,
    )
    assert resp.status_code == 200
    item = resp.json()["results"][0]
    assert item["status"] == "created", item
    return item["id"]


async def _create_term(client, auth_headers, terminology_id, value):
    resp = await client.post(
        f"{API}/terminologies/{terminology_id}/terms",
        json=[{"value": value, "label": value}],
        headers=auth_headers,
    )
    assert resp.status_code == 200
    item = resp.json()["results"][0]
    assert item["status"] == "created", item
    return item["id"]


async def _build_world(client, auth_headers):
    """GO_SLIM holds the colon-carrying terms; decoy 'GO' holds the term a
    2-part parse of REAL_VALUE would wrong-hit."""
    slim_id = await _create_terminology(client, auth_headers, "GO_SLIM")
    real_id = await _create_term(client, auth_headers, slim_id, REAL_VALUE)
    real_id_2 = await _create_term(client, auth_headers, slim_id, REAL_VALUE_2)
    decoy_tid = await _create_terminology(client, auth_headers, "GO")
    decoy_id = await _create_term(client, auth_headers, decoy_tid, DECOY_VALUE)
    return {
        "slim_id": slim_id,
        "real_id": real_id,
        "real_id_2": real_id_2,
        "decoy_tid": decoy_tid,
        "decoy_id": decoy_id,
    }


async def _get_term(client, auth_headers, term_id):
    resp = await client.get(f"{API}/terms/{term_id}", headers=auth_headers)
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# The 2-part form is rejected on every door
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_doors_reject_two_part_form(client: AsyncClient, auth_headers: dict):
    await _build_world(client, auth_headers)

    attempts = [
        ("PUT", f"{API}/terms", [{"term_id": REAL_VALUE, "label": "X"}]),
        ("POST", f"{API}/terms/deprecate", [{"term_id": REAL_VALUE, "reason": "r"}]),
        ("DELETE", f"{API}/terms", [{"id": REAL_VALUE}]),
        ("POST", f"{API}/ontology/term-relations",
         [{"source_term_id": REAL_VALUE, "target_term_id": REAL_VALUE_2,
           "relation_type": "is_a"}]),
        ("DELETE", f"{API}/ontology/term-relations",
         [{"source_term_id": REAL_VALUE, "target_term_id": REAL_VALUE_2,
           "relation_type": "is_a"}]),
    ]
    for method, url, body in attempts:
        resp = await client.request(
            method, url, json=body, headers=auth_headers,
            params={"namespace": "wip"},
        )
        assert resp.status_code == 422, (method, url, resp.text)
        detail = str(resp.json()["detail"])
        assert "ns:terminology:value" in detail, (method, url)
        assert "terminology=" in detail, (method, url)


@pytest.mark.asyncio
async def test_read_doors_reject_two_part_form(client: AsyncClient, auth_headers: dict):
    await _build_world(client, auth_headers)

    urls = [
        f"{API}/terms/{REAL_VALUE}",
        f"{API}/audit/terms/{REAL_VALUE}",
        f"{API}/ontology/terms/{REAL_VALUE}/children",
        f"{API}/ontology/terms/{REAL_VALUE}/parents",
        f"{API}/ontology/terms/{REAL_VALUE}/ancestors",
        f"{API}/ontology/terms/{REAL_VALUE}/descendants",
    ]
    for url in urls:
        resp = await client.get(url, headers=auth_headers, params={"namespace": "wip"})
        assert resp.status_code == 422, (url, resp.text)

    resp = await client.get(
        f"{API}/ontology/term-relations",
        headers=auth_headers,
        params={"namespace": "wip", "term_id": REAL_VALUE},
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# The field form lands on the right term — decoy present
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_via_field_form_hits_real_term_not_decoy(
    client: AsyncClient, auth_headers: dict
):
    world = await _build_world(client, auth_headers)

    resp = await client.put(
        f"{API}/terms",
        json=[{"term_id": REAL_VALUE, "label": "Mitotic cell cycle"}],
        headers=auth_headers,
        params={"namespace": "wip", "terminology": "GO_SLIM"},
    )
    assert resp.status_code == 200
    item = resp.json()["results"][0]
    assert item["status"] == "updated", item
    assert item["id"] == world["real_id"]

    assert (await _get_term(client, auth_headers, world["real_id"]))["label"] == "Mitotic cell cycle"
    assert (await _get_term(client, auth_headers, world["decoy_id"]))["label"] == DECOY_VALUE


@pytest.mark.asyncio
async def test_deprecate_via_field_form_resolves_replacement_in_scope(
    client: AsyncClient, auth_headers: dict
):
    world = await _build_world(client, auth_headers)

    resp = await client.post(
        f"{API}/terms/deprecate",
        json=[{
            "term_id": REAL_VALUE,
            "reason": "superseded",
            "replaced_by_term_id": REAL_VALUE_2,
        }],
        headers=auth_headers,
        params={"namespace": "wip", "terminology": "GO_SLIM"},
    )
    assert resp.status_code == 200
    assert resp.json()["results"][0]["status"] == "updated", resp.text

    term = await _get_term(client, auth_headers, world["real_id"])
    assert term["status"] == "deprecated"
    # The replacement pointer is persisted as the canonical id of the
    # in-scope term, not the decoy's and not the raw string.
    assert term["replaced_by_term_id"] == world["real_id_2"]


@pytest.mark.asyncio
async def test_delete_via_field_form_deletes_real_term_not_decoy(
    client: AsyncClient, auth_headers: dict
):
    world = await _build_world(client, auth_headers)

    resp = await client.request(
        "DELETE",
        f"{API}/terms",
        json=[{"id": REAL_VALUE}],
        headers=auth_headers,
        params={"namespace": "wip", "terminology": "GO_SLIM"},
    )
    assert resp.status_code == 200
    assert resp.json()["results"][0]["status"] == "deleted", resp.text

    assert (await _get_term(client, auth_headers, world["real_id"]))["status"] == "inactive"
    assert (await _get_term(client, auth_headers, world["decoy_id"]))["status"] == "active"


@pytest.mark.asyncio
async def test_relations_via_per_item_field_form(client: AsyncClient, auth_headers: dict):
    world = await _build_world(client, auth_headers)

    resp = await client.post(
        f"{API}/ontology/term-relations",
        json=[{
            "source_term_id": REAL_VALUE,
            "source_terminology": "GO_SLIM",
            "target_term_id": REAL_VALUE_2,
            "target_terminology": "GO_SLIM",
            "relation_type": "is_a",
        }],
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    assert resp.json()["results"][0]["status"] == "created", resp.text

    # The edge connects the GO_SLIM terms — canonical ids, decoy untouched.
    resp = await client.get(
        f"{API}/ontology/term-relations",
        headers=auth_headers,
        params={"namespace": "wip", "term_id": world["real_id"]},
    )
    assert resp.status_code == 200
    rels = resp.json()["items"]
    assert len(rels) == 1
    assert rels[0]["source_term_id"] == world["real_id"]
    assert rels[0]["target_term_id"] == world["real_id_2"]
    assert rels[0]["source_terminology_id"] == world["slim_id"]

    resp = await client.request(
        "DELETE",
        f"{API}/ontology/term-relations",
        json=[{
            "source_term_id": REAL_VALUE,
            "source_terminology": "GO_SLIM",
            "target_term_id": REAL_VALUE_2,
            "target_terminology": "GO_SLIM",
            "relation_type": "is_a",
        }],
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    assert resp.json()["results"][0]["status"] == "deleted", resp.text


# ---------------------------------------------------------------------------
# The other lossless forms, and the audit read door
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_three_part_qualified_form_works_on_writes(
    client: AsyncClient, auth_headers: dict
):
    world = await _build_world(client, auth_headers)

    resp = await client.put(
        f"{API}/terms",
        json=[{"term_id": f"wip:GO_SLIM:{REAL_VALUE}", "label": "Qualified hit"}],
        headers=auth_headers,
        params={"namespace": "wip"},
    )
    assert resp.status_code == 200
    item = resp.json()["results"][0]
    assert item["status"] == "updated", item
    assert item["id"] == world["real_id"]


@pytest.mark.asyncio
async def test_audit_read_via_field_form(client: AsyncClient, auth_headers: dict):
    world = await _build_world(client, auth_headers)

    resp = await client.get(
        f"{API}/audit/terms/{REAL_VALUE}",
        headers=auth_headers,
        params={"namespace": "wip", "terminology": "GO_SLIM"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert all(e["term_id"] == world["real_id"] for e in body["items"])
