"""if_match guards documents overwritten in place.

A versioned:false document stays at version 1, so if_match compared against
version guarded nothing: two PATCHes holding if_match=1 both answered
`updated` and the second silently replaced the first. if_match now compares
against the row's `revision`, which every in-place write moves on, and the
write itself is conditional on the revision read, so a write that lands
between the check and the write is caught too. On versioned templates the
revision is the version, so their if_match is unchanged.
"""

import pytest
from httpx import AsyncClient

from document_store.services.document_service import DocumentService

API = "/api/document-store"


async def _create(client: AsyncClient, headers: dict, note_id: str, body: str) -> dict:
    resp = await client.post(f"{API}/documents", headers=headers, json=[{
        "namespace": "wip", "template_id": "LATEST_ONLY_NOTE",
        "data": {"note_id": note_id, "body": body},
    }])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _patch(client: AsyncClient, headers: dict, doc_id: str, body: str, if_match=None) -> dict:
    item = {"document_id": doc_id, "patch": {"body": body}}
    if if_match is not None:
        item["if_match"] = if_match
    resp = await client.patch(f"{API}/documents", headers=headers, json=[item])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _get(client: AsyncClient, headers: dict, doc_id: str) -> dict:
    return (await client.get(f"{API}/documents/{doc_id}", headers=headers)).json()


@pytest.mark.asyncio
async def test_two_writers_holding_one_token_one_wins_one_conflicts(client, auth_headers):
    created = await _create(client, auth_headers, "N-1138-A", "v0")
    doc_id = created["document_id"]
    token = created["revision"]
    assert token == 1

    a = await _patch(client, auth_headers, doc_id, "writer A", if_match=token)
    assert a["status"] == "updated" and a["version"] == 1 and a["revision"] == 2
    b = await _patch(client, auth_headers, doc_id, "writer B", if_match=token)
    assert b["status"] == "error" and b["error_code"] == "concurrency_conflict", b

    doc = await _get(client, auth_headers, doc_id)
    assert doc["data"]["body"] == "writer A"
    assert (doc["version"], doc["revision"]) == (1, 2)

    # The fresh token works.
    c = await _patch(client, auth_headers, doc_id, "writer C", if_match=doc["revision"])
    assert c["status"] == "updated" and c["revision"] == 3


@pytest.mark.asyncio
async def test_a_write_between_the_check_and_the_write_is_caught(client, auth_headers, monkeypatch):
    """The token passes the step-3 check, then another write lands before
    the in-place write: the write must refuse, not overwrite."""
    created = await _create(client, auth_headers, "N-1138-B", "v0")
    doc_id = created["document_id"]

    original = DocumentService._relationship_refusal
    fired = {"done": False}

    async def interleave(self, *args, **kwargs):
        if not fired["done"]:
            fired["done"] = True
            # A concurrent writer without a token wins the race.
            await _patch(client, auth_headers, doc_id, "concurrent")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(DocumentService, "_relationship_refusal", interleave)
    held = await _patch(client, auth_headers, doc_id, "stale writer", if_match=1)
    assert held["status"] == "error" and held["error_code"] == "concurrency_conflict", held
    assert (await _get(client, auth_headers, doc_id))["data"]["body"] == "concurrent"


@pytest.mark.asyncio
async def test_a_patch_without_a_token_merges_onto_the_winner(client, auth_headers, monkeypatch):
    created = await _create(client, auth_headers, "N-1138-C", "v0")
    doc_id = created["document_id"]

    original = DocumentService._relationship_refusal
    fired = {"done": False}

    async def interleave(self, *args, **kwargs):
        if not fired["done"]:
            fired["done"] = True
            await _patch(client, auth_headers, doc_id, "first")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(DocumentService, "_relationship_refusal", interleave)
    second = await _patch(client, auth_headers, doc_id, "second")
    assert second["status"] == "updated" and second["revision"] == 3, second
    assert (await _get(client, auth_headers, doc_id))["data"]["body"] == "second"


@pytest.mark.asyncio
async def test_a_post_overwrite_moves_the_token(client, auth_headers):
    created = await _create(client, auth_headers, "N-1138-D", "v0")
    doc_id = created["document_id"]
    again = await _create(client, auth_headers, "N-1138-D", "v1")
    assert again["status"] == "updated" and again["revision"] == 2, again
    stale = await _patch(client, auth_headers, doc_id, "stale", if_match=1)
    assert stale["error_code"] == "concurrency_conflict", stale
    unchanged = await _create(client, auth_headers, "N-1138-D", "v1")
    assert unchanged["status"] == "unchanged" and unchanged["revision"] == 2, unchanged


@pytest.mark.asyncio
async def test_versioned_templates_keep_version_as_their_token(client, auth_headers):
    resp = await client.post(f"{API}/documents", headers=auth_headers, json=[{
        "namespace": "wip", "template_id": "PERSON",
        "data": {"national_id": "113800001", "first_name": "Rev", "last_name": "One"},
    }])
    created = resp.json()["results"][0]
    assert created["revision"] == created["version"] == 1
    patched = (await client.patch(f"{API}/documents", headers=auth_headers, json=[{
        "document_id": created["document_id"], "patch": {"first_name": "Rev2"}, "if_match": 1,
    }])).json()["results"][0]
    assert patched["status"] == "updated" and patched["version"] == patched["revision"] == 2
    doc = await _get(client, auth_headers, created["document_id"])
    assert doc["revision"] == doc["version"] == 2
