"""def-store's contracts say what the code does, and its counts are true.

- A term re-created with the same names in another order is unchanged.
- A skipped row is a success: it carries why (error_code) but no `error`.
- Batch sizes are bounded on every door (a 0 used to divide by zero).
- /validate says WHY a value is not valid, as a code.
- An unrecognised traversal `direction` is refused, not read as "both".
- Reconcile reports a failed update with its real error code, and a term
  that vanished mid-run as an error, not as updated/deprecated.
- A typed name cannot be validated without _NAME_TYPES: that is reported as
  the vocabulary missing, not as an unknown type.
- The startup name migration runs once; a stored terminology's retired
  `extensible` field is removed once.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from def_store.models.terminology import Terminology
from def_store.services.term_names import (
    NameTypeService,
    drop_retired_terminology_fields,
    migrate_legacy_term_names,
)
from def_store.services.terminology_service import TerminologyService

API = "/api/def-store"


async def _terminology(client, auth_headers, value):
    r = await client.post(f"{API}/terminologies", headers=auth_headers,
                          json=[{"value": value, "label": value, "namespace": "wip"}])
    assert r.json()["succeeded"] == 1, r.text
    return r.json()["results"][0]["id"]


@pytest.mark.asyncio
async def test_same_names_in_another_order_is_unchanged(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "ORDER_1182")
    names = [{"value": "alpha"}, {"value": "beta"}]
    r = await client.post(f"{API}/terminologies/{tid}/terms", headers=auth_headers,
                          json=[{"value": "t", "names": names}])
    assert r.json()["succeeded"] == 1

    r = await client.post(f"{API}/terminologies/{tid}/terms", headers=auth_headers,
                          params={"on_conflict": "validate"},
                          json=[{"value": "t", "names": list(reversed(names))}])

    assert r.json()["results"][0]["status"] == "unchanged", r.text


@pytest.mark.asyncio
async def test_skipped_rows_carry_no_error(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "SKIP_1182")
    await client.post(f"{API}/terminologies/{tid}/terms", headers=auth_headers,
                      json=[{"value": "a"}, {"value": "b"}])

    r = await client.post(f"{API}/terminologies/{tid}/terms", headers=auth_headers,
                          json=[{"value": "a"}, {"value": "c"}])

    skipped = r.json()["results"][0]
    assert skipped["status"] == "skipped", skipped
    assert skipped["error"] is None
    assert skipped["error_code"] == "already_exists"


@pytest.mark.asyncio
@pytest.mark.parametrize("param", ["batch_size", "registry_batch_size"])
async def test_batch_sizes_are_bounded(client: AsyncClient, auth_headers: dict, param):
    tid = await _terminology(client, auth_headers, f"BATCH_{param.upper()}_1182")

    r = await client.post(f"{API}/terminologies/{tid}/terms", headers=auth_headers,
                          params={param: 0}, json=[{"value": "a"}, {"value": "b"}])

    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_validate_says_why_as_a_code(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "VAL_1182")
    await client.post(f"{API}/terminologies/{tid}/terms", headers=auth_headers, json=[{"value": "ok"}])

    miss = (await client.post(f"{API}/validate", headers=auth_headers,
                              json={"terminology_id": tid, "value": "nope"})).json()
    hit = (await client.post(f"{API}/validate", headers=auth_headers,
                             json={"terminology_id": tid, "value": "ok"})).json()
    bulk = (await client.post(f"{API}/validate/bulk", headers=auth_headers, json={"items": [
        {"terminology_value": "NO_SUCH_TERMINOLOGY_1182", "value": "x"},
        {"value": "x"},
    ]})).json()["results"]

    assert miss["error_code"] == "term_not_found", miss
    assert hit.get("error_code") is None
    assert [b["error_code"] for b in bulk] == ["not_found", "invalid_request"], bulk


@pytest.mark.asyncio
async def test_unknown_direction_is_refused(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "DIR_1182")
    r = await client.post(f"{API}/terminologies/{tid}/terms", headers=auth_headers, json=[{"value": "a"}])
    term_id = r.json()["results"][0]["id"]

    r = await client.get(f"{API}/ontology/term-relations", headers=auth_headers,
                         params={"term_id": term_id, "direction": "banana", "namespace": "wip"})

    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_reconcile_reports_vanished_term_as_error(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "REC_1182")
    await client.post(f"{API}/terminologies/{tid}/terms", headers=auth_headers, json=[{"value": "a", "label": "A"}])

    async def vanished(*args, **kwargs):
        return None

    with patch.object(TerminologyService, "update_term", side_effect=vanished):
        r = await client.post(f"{API}/terminologies/{tid}/reconcile", headers=auth_headers,
                              json={"terms": [{"value": "a", "label": "A changed"}], "dry_run": False})

    body = r.json()
    assert body["counts"]["updated"] == 0, body
    assert body["counts"]["errors"] == 1
    item = next(x for x in body["results"] if x.get("value") == "a")
    assert item["status"] == "error" and item["error_code"] == "not_found", item


@pytest.mark.asyncio
async def test_typed_name_without_name_types_is_a_missing_vocabulary(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "NT_1182")
    await Terminology.find_one({"namespace": "wip", "value": "_NAME_TYPES"}).delete()
    NameTypeService.invalidate_name_type_cache()

    r = await client.post(f"{API}/terminologies/{tid}/terms", headers=auth_headers,
                          json=[{"value": "a", "names": [{"value": "A", "type": "SYNONYM"}]}])

    item = r.json()["results"][0]
    assert item["status"] == "error", item
    assert item["error_code"] == "internal_error"
    assert item["details"]["rule"] == "name_types_unavailable"
    NameTypeService.invalidate_name_type_cache()


@pytest.mark.asyncio
async def test_startup_passes_run_once(client: AsyncClient, auth_headers: dict):
    coll = Terminology.get_motor_collection()
    await coll.database["def_store_migrations"].delete_many({})
    tid = await _terminology(client, auth_headers, "LEGACY_EXT_1182")
    await coll.update_one({"terminology_id": tid}, {"$set": {"extensible": True}})

    assert await drop_retired_terminology_fields() == 1
    assert "extensible" not in await coll.find_one({"terminology_id": tid})
    await migrate_legacy_term_names()

    # Second boot: both passes are skipped by their markers
    await coll.update_one({"terminology_id": tid}, {"$set": {"extensible": True}})
    assert await drop_retired_terminology_fields() == 0
    markers = {m["_id"] async for m in coll.database["def_store_migrations"].find()}
    assert markers == {"term_names_v1", "terminology_extensible_removed_v1"}
