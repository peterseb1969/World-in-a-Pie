"""A document's identity-values synonym is qualified by its template.

Every synonym is a qualified key-value pair. The synonym minted from a
document's identity spellings carries the template's value under the
reserved "@template" key, so the same spelling under two templates is two
synonyms: a scene may be titled like its chapter.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from registry.main import app as registry_app

DOCS = "/api/document-store/documents"
NS = "wip"


async def _create(client, headers, template: str, title: str) -> dict:
    resp = await client.post(DOCS, headers=headers, json=[
        {"template_id": template, "namespace": NS, "data": {"title": title}},
    ])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _registry(method: str, path: str, headers: dict, body: list) -> dict:
    async with AsyncClient(transport=ASGITransport(app=registry_app), base_url="http://registry") as reg:
        resp = await reg.request(method, path, headers=headers, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _lookup(headers: dict, key: dict) -> dict:
    return await _registry("POST", "/api/registry/entries/lookup/by-key", headers, [
        {"namespace": NS, "entity_type": "documents", "composite_key": key},
    ])


@pytest.mark.asyncio
async def test_same_title_under_two_templates_is_two_documents(client: AsyncClient, auth_headers: dict):
    chapter = await _create(client, auth_headers, "CHAPTER_Q", "Low Water")
    scene = await _create(client, auth_headers, "SCENE_Q", "Low Water")
    assert chapter["status"] == "created", chapter
    assert scene["status"] == "created", scene
    assert chapter["id"] != scene["id"]


@pytest.mark.asyncio
async def test_the_synonym_is_qualified_by_template(client: AsyncClient, auth_headers: dict):
    chapter = await _create(client, auth_headers, "CHAPTER_Q", "Harbour")
    assert chapter["status"] == "created", chapter

    found = await _lookup(auth_headers, {"@template": "CHAPTER_Q", "title": "Harbour"})
    assert found["status"] == "found"
    assert found["entry_id"] == chapter["id"]

    # The bare spelling is no longer a synonym: it would claim "Harbour" for
    # every template in the namespace.
    assert (await _lookup(auth_headers, {"title": "Harbour"}))["status"] == "not_found"
