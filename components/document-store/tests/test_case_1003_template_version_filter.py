"""GET /documents filters by the template version a row is pinned to.

Template-store asks for the active documents on one template version before
deactivating it (CASE-1003): documents on other versions of the template do
not depend on that version.
"""

import pytest

DOCS = "/api/document-store/documents"


def _person(national_id: str) -> dict:
    return {
        "national_id": national_id, "first_name": "John", "last_name": "Doe",
        "birth_date": "1990-01-15", "gender": "M", "age": 34,
    }


async def _count(client, auth_headers, **params) -> int:
    resp = await client.get(DOCS, headers=auth_headers, params={
        "namespace": "wip", "template_value": "PERSON", "status": "active", "page_size": 1, **params,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["total"]


@pytest.mark.asyncio
async def test_template_version_filter_counts_only_rows_on_that_version(client, auth_headers):
    resp = await client.post(DOCS, headers=auth_headers, json=[
        {"namespace": "wip", "template_id": "PERSON", "data": _person(f"10030000{i}")} for i in range(3)
    ])
    results = resp.json()["results"]
    assert all(r["status"] == "created" for r in results), results
    pinned = (await client.get(f"{DOCS}/{results[0]['document_id']}", headers=auth_headers)).json()
    version = pinned["template_version"]

    assert await _count(client, auth_headers) == 3
    assert await _count(client, auth_headers, template_version=version) == 3
    assert await _count(client, auth_headers, template_version=version + 1) == 0


@pytest.mark.asyncio
async def test_template_version_must_be_positive(client, auth_headers):
    resp = await client.get(DOCS, headers=auth_headers, params={"namespace": "wip", "template_version": 0})
    assert resp.status_code == 422
