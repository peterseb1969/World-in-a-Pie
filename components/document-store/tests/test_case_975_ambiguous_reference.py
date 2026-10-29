"""CASE-975 — a bare document reference resolves to one document or is refused.

A bare string in a document reference field is resolved through the
Registry's candidates: every document whose keys carry the value. The
field's target_templates narrow them; one survivor resolves, several are an
`ambiguous_reference` error listing the candidates. Before, the Registry
answered with whichever entry it found first.

Two sources of several candidates are pinned here: the same identity
spelling under two templates, and a template's value, which every one of
its documents carries in its auto-synonym.
"""

import pytest
from httpx import AsyncClient

DOCS = "/api/document-store/documents"


async def _post(client: AsyncClient, auth_headers: dict, items: list[dict]) -> list[dict]:
    payload = [{"namespace": "wip", **item} for item in items]
    response = await client.post(DOCS, headers=auth_headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["results"]


async def _titled(client, auth_headers, template: str, title: str) -> str:
    [created] = await _post(client, auth_headers, [{"template_id": template, "data": {"title": title}}])
    assert created["status"] == "created", created
    return created["document_id"]


def _holder(name: str, ref: str) -> dict:
    return {"template_id": "HOLDER_Q", "data": {"name": name, "ref": ref}}


async def _reference(client, auth_headers, document_id: str) -> dict:
    response = await client.get(f"{DOCS}/{document_id}", headers=auth_headers)
    assert response.status_code == 200, response.text
    [ref] = response.json()["references"]
    return ref["resolved"]


@pytest.mark.asyncio
async def test_target_templates_narrow_a_shared_spelling(client: AsyncClient, auth_headers: dict):
    chapter = await _titled(client, auth_headers, "CHAPTER_Q", "Low Water")
    await _titled(client, auth_headers, "SCENE_Q", "Low Water")

    [held] = await _post(client, auth_headers, [_holder("H1", "Low Water")])
    assert held["status"] == "created", held
    assert (await _reference(client, auth_headers, held["document_id"]))["document_id"] == chapter


@pytest.mark.asyncio
async def test_several_documents_of_the_target_template_are_ambiguous(
    client: AsyncClient, auth_headers: dict,
):
    """Every CHAPTER_Q document carries "CHAPTER_Q" in its auto-synonym; the
    old lookup resolved the bare value to whichever came first."""
    first = await _titled(client, auth_headers, "CHAPTER_Q", "Ebb")
    second = await _titled(client, auth_headers, "CHAPTER_Q", "Flood")

    [held] = await _post(client, auth_headers, [_holder("H2", "CHAPTER_Q")])
    assert held["status"] == "error", held
    assert held["error_code"] == "validation_failed", held
    [error] = [e for e in held["details"]["errors"] if e["code"] == "ambiguous_reference"]
    assert error["field"] == "ref"
    assert {c["document_id"] for c in error["details"]["candidates"]} == {first, second}
    assert {c["template_value"] for c in error["details"]["candidates"]} == {"CHAPTER_Q"}


@pytest.mark.asyncio
async def test_a_document_id_is_never_ambiguous(client: AsyncClient, auth_headers: dict):
    first = await _titled(client, auth_headers, "CHAPTER_Q", "Slack")
    await _titled(client, auth_headers, "CHAPTER_Q", "Neap")
    [held] = await _post(client, auth_headers, [_holder("H3", first)])
    assert held["status"] == "created", held
    assert (await _reference(client, auth_headers, held["document_id"]))["document_id"] == first


@pytest.mark.asyncio
async def test_bulk_reports_every_ambiguous_item_as_ambiguous(client: AsyncClient, auth_headers: dict):
    """The batch's reference cache must not turn the second lookup of an
    ambiguous value into reference_not_found."""
    await _titled(client, auth_headers, "CHAPTER_Q", "Spring")
    await _titled(client, auth_headers, "CHAPTER_Q", "Autumn")
    results = await _post(client, auth_headers, [
        _holder("H4", "CHAPTER_Q"), _holder("H5", "CHAPTER_Q"),
    ])
    for item in results:
        assert item["status"] == "error", item
        codes = {e["code"] for e in item["details"]["errors"]}
        assert codes == {"ambiguous_reference"}, item
