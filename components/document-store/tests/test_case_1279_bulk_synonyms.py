"""A bulk create's auto-synonym stage (Stage 4c) runs after the batch's
documents are written, so neither an unparseable Registry answer nor an
unexpected failure of the stage may become a whole-batch 500.
"""

from unittest.mock import MagicMock

import httpx
import pytest
from httpx import AsyncClient

from document_store.services.document_service import DocumentService
from document_store.services.registry_client import RegistryClient

API = "/api/document-store"


@pytest.mark.asyncio
async def test_an_unparseable_registry_answer_refuses_each_document(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>proxy error</html>")

    client = RegistryClient(base_url="http://registry.test", api_key="k")
    monkeypatch.setattr(
        client, "_make_client",
        MagicMock(return_value=httpx.AsyncClient(transport=httpx.MockTransport(handler))),
    )
    refusals = await client.register_auto_synonyms_per_item(
        [{"document_id": "D1", "template_value": "T", "identity_hash": "h",
          "has_identity_fields": True}],
        namespace="ns",
    )
    assert refusals == ["Failed to register auto-synonym: unreadable Registry response"]


@pytest.mark.asyncio
async def test_a_failing_synonym_stage_warns_instead_of_failing_the_batch(
    client: AsyncClient, auth_headers, monkeypatch,
):
    async def boom(self, *args, **kwargs):
        raise RuntimeError("stage 4c broke")

    monkeypatch.setattr(DocumentService, "_register_bulk_auto_synonyms", boom)
    items = [
        {"namespace": "wip", "template_id": "PERSON",
         "data": {"national_id": f"12790000{i}", "first_name": "A", "last_name": "B"}}
        for i in (2, 3)
    ]
    resp = await client.post(f"{API}/documents", headers=auth_headers, json=items)
    assert resp.status_code == 200, resp.text
    for row in resp.json()["results"]:
        assert row["status"] == "created", row
        assert any("Auto-synonym registration did not complete" in w for w in row["warnings"]), row


@pytest.mark.asyncio
async def test_a_rolled_back_document_is_never_announced(
    client: AsyncClient, auth_headers, monkeypatch,
):
    """A batch creates a versioned:false document and overwrites it in place;
    its auto-synonym is refused, so the batch rolls it back. The in-place
    overwrite's event must not have gone out already."""
    import document_store.services.document_service as ds

    published: list[tuple] = []
    original_publish = ds.publish_document_event

    async def record(event_type, payload, **kwargs):
        published.append((event_type, payload.get("document_id")))
        return await original_publish(event_type, payload, **kwargs)

    async def refuse(self, documents, namespace, created_by=None):
        return ["refused for the test"] * len(documents)

    monkeypatch.setattr(ds, "publish_document_event", record)
    monkeypatch.setattr(RegistryClient, "register_auto_synonyms_per_item", refuse)
    note = {"namespace": "wip", "template_id": "LATEST_ONLY_NOTE"}
    resp = await client.post(f"{API}/documents", headers=auth_headers, json=[
        {**note, "data": {"note_id": "N-1279-R10", "body": "first"}},
        {**note, "data": {"note_id": "N-1279-R10", "body": "second"}},
    ])
    assert resp.status_code == 200, resp.text
    rows = resp.json()["results"]
    assert {r["status"] for r in rows} == {"error"}, rows
    assert published == [], published
