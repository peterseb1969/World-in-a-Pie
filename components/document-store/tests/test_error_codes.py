"""document-store's bulk error items carry a machine-readable error_code.

Callers branch on the code, not the English message. A one-item POST runs
the single create path, two or more the bulk path: both must report the same
code for the same failure. These also pin the not-found codes of the
delete/archive routes, and the source gate over the whole package.
"""

from pathlib import Path

import pytest
from httpx import AsyncClient

import document_store
from wip_auth.testing import assert_error_items_carry_codes

DOCS = "/api/document-store/documents"
MISSING_ID = "0190c000-0000-7000-0000-0000000000ff"


def test_error_items_carry_codes():
    assert_error_items_carry_codes(Path(document_store.__file__).parent)


def _invalid_person() -> dict:
    # age must be an integer: a validation failure, not a missing reference
    return {"template_id": "PERSON", "namespace": "wip", "data": {
        "national_id": "987654321", "first_name": "Ann", "last_name": "Lee",
        "birth_date": "1991-02-03", "gender": "F", "age": "not-a-number",
    }}


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2], ids=["single_path", "bulk_path"])
async def test_validation_failure_is_validation_failed(client: AsyncClient, auth_headers: dict, count: int):
    resp = await client.post(DOCS, headers=auth_headers, json=[_invalid_person()] * count)
    assert resp.status_code == 200, resp.text
    item = resp.json()["results"][0]
    assert item["status"] == "error"
    assert item["error_code"] == "validation_failed"
    assert item["details"]["errors"], item


@pytest.mark.asyncio
async def test_delete_and_archive_missing_document_are_not_found(client: AsyncClient, auth_headers: dict):
    resp = await client.request("DELETE", DOCS, headers=auth_headers, json=[{"id": MISSING_ID}])
    assert resp.json()["results"][0]["error_code"] == "not_found"
    resp = await client.post(f"{DOCS}/archive", headers=auth_headers, json=[{"id": MISSING_ID}])
    assert resp.json()["results"][0]["error_code"] == "not_found"
