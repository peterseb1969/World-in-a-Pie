"""Re-posting a soft-deleted identity brings it back as a proper successor.

A write to an existing identity follows its latest version, whatever its
status. When that version is soft-deleted the write brings the document
back: a successor version that keeps the entity's ``created_at``, carries
``metadata.custom`` forward when the caller does not address it, reports
``updated``, and does not release the deleted version's file references a
second time. A revival is a change even when the data is identical.

Before, an active-only lookup missed the deleted document, the write was
taken for a brand-new one, collided with version 1, and the duplicate-key
retry pushed it to the next version: same id and number, but a fresh
``created_at`` and dropped metadata.

Both write paths are covered: a single-item POST takes ``create_document``,
two or more items take the bulk path.
"""

import pytest
from httpx import AsyncClient

PERSON = "PERSON"
NOTE = "LATEST_ONLY_NOTE"


def _person(national_id: str, first_name: str = "John") -> dict:
    return {
        "national_id": national_id,
        "first_name": first_name,
        "last_name": "Doe",
        "birth_date": "1990-01-15",
        "gender": "M",
        "age": 34,
    }


async def _post(client: AsyncClient, auth_headers: dict, items: list[dict]) -> list[dict]:
    payload = [{"namespace": "wip", **item} for item in items]
    response = await client.post(
        "/api/document-store/documents", headers=auth_headers, json=payload
    )
    assert response.status_code == 200, response.text
    return response.json()["results"]


async def _delete(client: AsyncClient, auth_headers: dict, document_id: str) -> None:
    response = await client.request(
        "DELETE", "/api/document-store/documents",
        headers=auth_headers, json=[{"id": document_id}],
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["status"] == "deleted"


async def _versions(client: AsyncClient, auth_headers: dict, document_id: str) -> list[dict]:
    response = await client.get(
        f"/api/document-store/documents/{document_id}/versions", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    return sorted(response.json()["versions"], key=lambda v: v["version"])


async def _doc(client: AsyncClient, auth_headers: dict, document_id: str) -> dict:
    response = await client.get(
        f"/api/document-store/documents/{document_id}", headers=auth_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _revive(client, auth_headers, item, *, bulk: bool) -> dict:
    """Post ``item``, alone or beside an unrelated document (the bulk path)."""
    items = [item]
    if bulk:
        items.append({"template_id": PERSON, "data": _person("999999999", "Other")})
    return (await _post(client, auth_headers, items))[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
async def test_a_deleted_identity_comes_back_as_a_proper_successor(
    client: AsyncClient, auth_headers: dict, bulk: bool
):
    national_id = "111111111" if not bulk else "222222222"
    [created] = await _post(client, auth_headers, [{
        "template_id": PERSON, "data": _person(national_id),
        "metadata": {"tag": "keep-me"},
    }])
    document_id = created["document_id"]
    await _delete(client, auth_headers, document_id)

    result = await _revive(client, auth_headers, {
        "template_id": PERSON, "data": _person(national_id, "Jane"),
    }, bulk=bulk)

    assert result["status"] == "updated"
    assert result["document_id"] == document_id
    assert result["version"] == 2
    v1, v2 = await _versions(client, auth_headers, document_id)
    assert v1["status"] == "inactive"
    assert v2["status"] == "active"
    # The entity's creation time carries forward: it is not a new document.
    assert v2["created_at"] == v1["created_at"]
    doc = await _doc(client, auth_headers, document_id)
    assert doc["data"]["first_name"] == "Jane"
    # Not addressed by the caller, so carried forward like any update.
    assert doc["metadata"]["custom"] == {"tag": "keep-me"}


@pytest.mark.asyncio
@pytest.mark.parametrize("bulk", [False, True], ids=["single", "bulk"])
async def test_identical_data_still_revives(
    client: AsyncClient, auth_headers: dict, bulk: bool
):
    # Re-posting byte-identical data to a deleted document is a revival, not
    # a no-op: answering "unchanged" would leave it deleted.
    national_id = "333333333" if not bulk else "444444444"
    [created] = await _post(client, auth_headers, [
        {"template_id": PERSON, "data": _person(national_id)},
    ])
    await _delete(client, auth_headers, created["document_id"])

    result = await _revive(client, auth_headers, {
        "template_id": PERSON, "data": _person(national_id),
    }, bulk=bulk)

    assert result["status"] == "updated"
    assert result["version"] == 2
    doc = await _doc(client, auth_headers, created["document_id"])
    assert doc["status"] == "active"


@pytest.mark.asyncio
async def test_an_active_identity_with_identical_data_is_still_unchanged(
    client: AsyncClient, auth_headers: dict
):
    # The revival bypass must not touch the ordinary no-op.
    [created] = await _post(client, auth_headers, [
        {"template_id": PERSON, "data": _person("555555555")},
    ])
    [again] = await _post(client, auth_headers, [
        {"template_id": PERSON, "data": _person("555555555")},
    ])
    assert again["status"] == "unchanged"
    assert again["version"] == created["version"] == 1


@pytest.mark.asyncio
async def test_an_unversioned_template_revives_in_place(
    client: AsyncClient, auth_headers: dict
):
    # versioned: false overwrites in place; a deleted document comes back at
    # the same version, active again.
    [created] = await _post(client, auth_headers, [
        {"template_id": NOTE, "data": {"note_id": "N949", "body": "first"}},
    ])
    document_id = created["document_id"]
    await _delete(client, auth_headers, document_id)

    [result] = await _post(client, auth_headers, [
        {"template_id": NOTE, "data": {"note_id": "N949", "body": "back"}},
    ])

    assert result["status"] == "updated"
    assert result["document_id"] == document_id
    assert result["version"] == 1
    doc = await _doc(client, auth_headers, document_id)
    assert doc["status"] == "active"
    assert doc["data"]["body"] == "back"
