"""A Registry write that cannot reach the Registry is a per-item
registry_error, never a bare 500.

document-store's Registry client let an httpx transport error escape its
write calls (ID registration, synonym add / remove, identity re-key), so a
dropped connection surfaced as an unhandled exception. It now raises
RegistryError, as an error status already did, and every caller reports it
per item. A single create whose auto-synonym registration fails rolls the
document back and refuses the item with registry_error — it re-raised, a 500
— as the bulk path already did.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from document_store.services.registry_client import RegistryClient, RegistryError
from wip_auth.registry_client import clear_registry_transport, set_registry_transport


@pytest.fixture
def unreachable_registry():
    import wip_auth.registry_client as base

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    previous = base._registry_transport
    set_registry_transport(httpx.MockTransport(refuse))
    yield
    if previous is None:
        clear_registry_transport()
    else:
        set_registry_transport(previous)


def _client() -> RegistryClient:
    return RegistryClient(base_url="http://registry.test", api_key="k")


CALLS: list[tuple[str, Any]] = [
    ("generate_document_id", lambda c: c.generate_document_id("T1", "ns", {"a": 1})),
    ("generate_document_ids_bulk", lambda c: c.generate_document_ids_bulk([{"template_id": "T1"}], "ns")),
    ("rekey_identity", lambda c: c.rekey_identity([{"entry_id": "d1", "identity_hash": "h"}])),
    ("add_synonyms", lambda c: c.add_synonyms("d1", "ns", "documents", [{"k": "v"}])),
    ("remove_synonyms", lambda c: c.remove_synonyms("d1", "ns", "documents", [{"k": "v"}])),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [c for _, c in CALLS], ids=[n for n, _ in CALLS])
async def test_an_unreachable_registry_is_a_registry_error(unreachable_registry, call):
    with pytest.raises(RegistryError, match="Registry unreachable"):
        await call(_client())


@pytest.mark.asyncio
async def test_a_single_create_whose_auto_synonym_fails_is_refused_and_rolled_back(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    async def refuse(self, **kwargs):
        raise RegistryError("Failed to register auto-synonym: 503 - down")

    monkeypatch.setattr(RegistryClient, "register_auto_synonym", refuse)
    item = {"template_id": "CHAPTER_Q", "namespace": "wip", "data": {"title": "Synonym 1139"}}
    response = await client.post("/api/document-store/documents", headers=auth_headers, json=[item])
    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["status"] == "error"
    assert result["error_code"] == "registry_error", result

    from document_store.models.document import Document

    assert await Document.find({"data.title": "Synonym 1139"}).count() == 0
