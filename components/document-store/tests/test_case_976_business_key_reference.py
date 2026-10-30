"""A business-key (dict) document reference resolves to the one document whose
identity it is, through the Registry.

The dict path used to query Mongo directly: `{"status": "active",
"data.<identity field>": …}` with no namespace and no template, skipping
identity fields the dict left out and comparing the submitted spelling. It
picked documents of other namespaces (and then failed the template check
although the right document existed), matched an arbitrary document for `{}`
or a misspelt key, and missed a document referenced through an alias of its
term identity. Now the dict must name a target template's identity fields
exactly; its values are resolved and hashed as a document write does, and
the document is looked up by its Registry primary key in the referencing
document's namespace.
"""

import pytest
from httpx import AsyncClient

from document_store.services.document_service import get_document_service

DOCS = "/api/document-store/documents"


async def _post(client: AsyncClient, auth_headers: dict, items: list[dict]) -> list[dict]:
    payload = [{"namespace": "wip", **item} for item in items]
    response = await client.post(DOCS, headers=auth_headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()["results"]


async def _create(client, auth_headers, template: str, data: dict) -> str:
    [created] = await _post(client, auth_headers, [{"template_id": template, "data": data}])
    assert created["status"] in ("created", "updated"), created
    return created["document_id"]


async def _resolved(client, auth_headers, document_id: str) -> dict:
    response = await client.get(f"{DOCS}/{document_id}", headers=auth_headers)
    assert response.status_code == 200, response.text
    [ref] = response.json()["references"]
    return ref["resolved"]


def _errors(item: dict, code: str) -> list[dict]:
    assert item["status"] == "error", item
    return [e for e in item["details"]["errors"] if e["code"] == code]


@pytest.mark.asyncio
async def test_a_dict_reference_resolves_its_document(client: AsyncClient, auth_headers: dict):
    chapter = await _create(client, auth_headers, "CHAPTER_Q", {"title": "Riverbend"})
    [held] = await _post(client, auth_headers, [
        {"template_id": "HOLDER_Q", "data": {"name": "B1", "ref": {"title": "Riverbend"}}},
    ])
    assert held["status"] == "created", held
    assert (await _resolved(client, auth_headers, held["document_id"]))["document_id"] == chapter


@pytest.mark.asyncio
async def test_a_document_of_another_template_with_the_same_values_is_not_picked(
    client: AsyncClient, auth_headers: dict,
):
    """SCENE_Q shares the identity field name; the old query had no template
    filter and could return the scene, then fail the template check."""
    await _create(client, auth_headers, "SCENE_Q", {"title": "Crossing"})
    chapter = await _create(client, auth_headers, "CHAPTER_Q", {"title": "Crossing"})
    [held] = await _post(client, auth_headers, [
        {"template_id": "HOLDER_Q", "data": {"name": "B2", "ref": {"title": "Crossing"}}},
    ])
    assert held["status"] == "created", held
    assert (await _resolved(client, auth_headers, held["document_id"]))["document_id"] == chapter


@pytest.mark.asyncio
async def test_another_namespace_is_not_searched(client: AsyncClient, auth_headers: dict):
    chapter = await _create(client, auth_headers, "CHAPTER_Q", {"title": "Elsewhere"})
    template_id = (await client.get(f"{DOCS}/{chapter}", headers=auth_headers)).json()["template_id"]
    vs = get_document_service().validation_service
    found, _ = await vs._resolve_business_key_reference(
        {"title": "Elsewhere"}, [template_id], "wip", None,
    )
    assert found is not None and found.document_id == chapter
    elsewhere, error = await vs._resolve_business_key_reference(
        {"title": "Elsewhere"}, [template_id], "other-ns", None,
    )
    assert elsewhere is None and error is None


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [{}, {"titel": "Riverbend"}, {"title": "Riverbend", "extra": 1}],
                         ids=["empty", "misspelt", "extra-field"])
async def test_a_key_that_is_not_the_identity_is_refused(client: AsyncClient, auth_headers: dict, key):
    await _create(client, auth_headers, "CHAPTER_Q", {"title": "Riverbend"})
    [held] = await _post(client, auth_headers, [
        {"template_id": "HOLDER_Q", "data": {"name": "B3", "ref": key}},
    ])
    [error] = _errors(held, "invalid_reference_value")
    assert error["field"] == "ref"
    assert error["details"]["identity_fields"] == {"CHAPTER_Q": ["title"]}


@pytest.mark.asyncio
async def test_an_alias_of_a_term_identity_finds_the_document(client: AsyncClient, auth_headers: dict):
    """In the def-store double "m" and "M" are aliases of one term: the
    identity is the term, so either spelling names the same document."""
    target = await _create(client, auth_headers, "TERM_IDENTITY", {"category": "M"})
    [held] = await _post(client, auth_headers, [
        {"template_id": "HOLDER_TERM_Q", "data": {"name": "B4", "ref": {"category": "m"}}},
    ])
    assert held["status"] == "created", held
    assert (await _resolved(client, auth_headers, held["document_id"]))["document_id"] == target


@pytest.mark.asyncio
async def test_matches_under_two_target_templates_are_ambiguous(client: AsyncClient, auth_headers: dict):
    chapter = await _create(client, auth_headers, "CHAPTER_Q", {"title": "Delta"})
    scene = await _create(client, auth_headers, "SCENE_Q", {"title": "Delta"})
    [held] = await _post(client, auth_headers, [
        {"template_id": "HOLDER_TWO_Q", "data": {"name": "B5", "ref": {"title": "Delta"}}},
    ])
    [error] = _errors(held, "ambiguous_reference")
    assert {c["document_id"] for c in error["details"]["candidates"]} == {chapter, scene}


@pytest.mark.asyncio
async def test_one_match_among_two_target_templates_resolves(client: AsyncClient, auth_headers: dict):
    scene = await _create(client, auth_headers, "SCENE_Q", {"title": "Estuary"})
    [held] = await _post(client, auth_headers, [
        {"template_id": "HOLDER_TWO_Q", "data": {"name": "B6", "ref": {"title": "Estuary"}}},
    ])
    assert held["status"] == "created", held
    assert (await _resolved(client, auth_headers, held["document_id"]))["document_id"] == scene


@pytest.mark.asyncio
async def test_no_document_is_reference_not_found(client: AsyncClient, auth_headers: dict):
    [held] = await _post(client, auth_headers, [
        {"template_id": "HOLDER_Q", "data": {"name": "B7", "ref": {"title": "Nowhere"}}},
    ])
    assert _errors(held, "reference_not_found")
