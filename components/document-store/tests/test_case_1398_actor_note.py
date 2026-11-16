"""A document write's actor is the authenticated caller; the caller's text is actor_note.

Document-store always recorded the authenticated caller as created_by /
updated_by, but its request models still offered `created_by` (create) and
`updated_by` (delete) as "the user", and dropped what a caller sent there
without a word. As in the Registry, that text is now kept as the row's
`actor_note`: set by the write that made a version or the delete that
retired it, and cleared by a write that sends none.
"""

import pytest
from httpx import AsyncClient

DOCS = "/api/document-store/documents"


async def _create(client: AsyncClient, auth_headers: dict, items: list[dict]) -> list[dict]:
    resp = await client.post(DOCS, headers=auth_headers, json=[
        {"namespace": "wip", "template_id": "PERSON", **item} for item in items
    ])
    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert all(r["status"] in ("created", "updated") for r in results), results
    return results


async def _get(client: AsyncClient, auth_headers: dict, document_id: str) -> dict:
    resp = await client.get(f"{DOCS}/{document_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _person(national_id: str, first_name: str = "John") -> dict:
    return {"national_id": national_id, "first_name": first_name, "last_name": "Doe",
            "birth_date": "1990-01-15", "gender": "M", "age": 34}


@pytest.mark.asyncio
async def test_a_create_keeps_the_sent_text_as_actor_note(client, auth_headers):
    [result] = await _create(client, auth_headers, [
        {"data": _person("139800001"), "created_by": "nightly loader"},
    ])
    doc = await _get(client, auth_headers, result["document_id"])
    assert doc["actor_note"] == "nightly loader"
    assert doc["created_by"] != "nightly loader"
    assert doc["created_by"] == doc["updated_by"]


@pytest.mark.asyncio
async def test_each_item_of_a_bulk_create_keeps_its_own_text(client, auth_headers):
    first, second = await _create(client, auth_headers, [
        {"data": _person("139800002"), "created_by": "importer A"},
        {"data": _person("139800003")},
    ])
    assert (await _get(client, auth_headers, first["document_id"]))["actor_note"] == "importer A"
    assert (await _get(client, auth_headers, second["document_id"]))["actor_note"] is None


@pytest.mark.asyncio
async def test_a_new_version_carries_its_own_writes_text(client, auth_headers):
    [v1] = await _create(client, auth_headers, [
        {"data": _person("139800004"), "created_by": "first"},
    ])
    [v2] = await _create(client, auth_headers, [
        {"data": _person("139800004", "Jane"), "created_by": "second"},
    ])
    assert v2["document_id"] == v1["document_id"] and v2["version"] == 2
    assert (await _get(client, auth_headers, v1["document_id"]))["actor_note"] == "second"

    # A PATCH sends no text on who acted, so the version it writes has none.
    resp = await client.patch(DOCS, headers=auth_headers, json=[
        {"document_id": v1["document_id"], "patch": {"first_name": "June"}},
    ])
    assert resp.json()["results"][0]["status"] == "updated", resp.text
    assert (await _get(client, auth_headers, v1["document_id"]))["actor_note"] is None


@pytest.mark.asyncio
async def test_a_delete_keeps_its_text_on_the_retired_row(client, auth_headers):
    [result] = await _create(client, auth_headers, [
        {"data": _person("139800005"), "created_by": "created so"},
    ])
    resp = await client.request("DELETE", DOCS, headers=auth_headers, json=[
        {"id": result["document_id"], "updated_by": "cleanup job"},
    ])
    assert resp.json()["results"][0]["status"] == "deleted", resp.text
    doc = await _get(client, auth_headers, result["document_id"])
    assert doc["status"] == "inactive"
    assert doc["actor_note"] == "cleanup job"
    assert doc["updated_by"] != "cleanup job"


@pytest.mark.asyncio
async def test_an_overwrite_in_place_replaces_the_note(client, auth_headers):
    """A versioned: false template overwrites its one version, so the note is
    the last write's text, and a write without text clears it."""
    def note(body, created_by=None):
        item = {"namespace": "wip", "template_id": "LATEST_ONLY_NOTE",
                "data": {"note_id": "n-1398", "body": body}}
        if created_by is not None:
            item["created_by"] = created_by
        return [item]

    first = await client.post(DOCS, headers=auth_headers, json=note("a", "writer one"))
    doc_id = first.json()["results"][0]["document_id"]
    await client.post(DOCS, headers=auth_headers, json=note("b", "writer two"))
    doc = await _get(client, auth_headers, doc_id)
    assert doc["version"] == 1 and doc["actor_note"] == "writer two"

    await client.post(DOCS, headers=auth_headers, json=note("c"))
    assert (await _get(client, auth_headers, doc_id))["actor_note"] is None
