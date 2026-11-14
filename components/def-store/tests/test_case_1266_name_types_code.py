"""A bulk term create (two or more items) and a reconcile report a missing
_NAME_TYPES vocabulary as name_types_unavailable, as the single-item path
does: the error is the platform's, not the names'."""

import pytest
from httpx import AsyncClient

from def_store.models.terminology import Terminology
from def_store.services.term_names import NameTypeService

API = "/api/def-store"


async def _terminology(client, headers, value):
    r = await client.post(f"{API}/terminologies", headers=headers,
                          json=[{"value": value, "label": value, "namespace": "wip"}])
    assert r.status_code == 200, r.text
    return r.json()["results"][0]["id"]


async def _without_name_types():
    await Terminology.find_one({"namespace": "wip", "value": "_NAME_TYPES"}).delete()
    NameTypeService.invalidate_name_type_cache()


def _assert_unavailable(item):
    assert item["status"] == "error", item
    assert item["error_code"] == "internal_error", item
    assert item["details"]["rule"] == "name_types_unavailable", item


@pytest.mark.asyncio
async def test_a_bulk_create_reports_the_missing_vocabulary(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "NT_BULK_1266")
    await _without_name_types()
    try:
        r = await client.post(f"{API}/terminologies/{tid}/terms", headers=auth_headers, json=[
            {"value": "a", "names": [{"value": "A", "type": "SYNONYM"}]},
            {"value": "b"},
        ])
        _assert_unavailable(r.json()["results"][0])
    finally:
        NameTypeService.invalidate_name_type_cache()


@pytest.mark.asyncio
async def test_a_reconcile_reports_the_missing_vocabulary(client: AsyncClient, auth_headers: dict):
    tid = await _terminology(client, auth_headers, "NT_REC_1266")
    await _without_name_types()
    try:
        r = await client.post(f"{API}/terminologies/{tid}/reconcile", headers=auth_headers, json={
            "terms": [{"value": "a", "names": [{"value": "A", "type": "SYNONYM"}]}],
            "dry_run": False,
        })
        item = next(x for x in r.json()["results"] if x.get("value") == "a")
        _assert_unavailable(item)
    finally:
        NameTypeService.invalidate_name_type_cache()
