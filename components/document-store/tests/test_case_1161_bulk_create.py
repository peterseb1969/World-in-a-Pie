"""A bulk create's Registry failures and its placement fallback, pinned before
_bulk_create's refactor.

Provenance: CASE-1161. The suite never reached these branches of
DocumentService._bulk_create: the bulk id request to the Registry failing as a
whole, the Registry answering one item with an error, and the batch
reference placement falling back to per-item lookups when the Registry
cannot be asked. The real Registry is mounted in-process; each test breaks
one call to it.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from wip_auth.registry_client import RegistryUnavailableError

from document_store.services.reference_validator import ReferenceValidator
from document_store.services.registry_client import RegistryClient, RegistryError

URL = "/api/document-store/documents"


def _person(nid: str, first: str | None = "Ann") -> dict:
    data = {"national_id": nid, "last_name": "Lee"}
    if first is not None:
        data["first_name"] = first
    return {"template_id": "PERSON", "namespace": "wip", "data": data}


async def _post(client, headers, items) -> list[dict]:
    resp = await client.post(URL, headers=headers, json=items)
    assert resp.status_code == 200, resp.text
    return sorted(resp.json()["results"], key=lambda r: r["index"])


@pytest.mark.asyncio
async def test_a_failed_registry_request_fails_every_valid_item(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    async def fail(*_args, **_kwargs):
        raise RegistryError("registry down")

    monkeypatch.setattr(RegistryClient, "generate_document_ids_bulk", fail)
    results = await _post(client, auth_headers, [
        _person("116100001"), _person("116100002", first=None), _person("116100003"),
    ])
    assert [r["status"] for r in results] == ["error", "error", "error"]
    # The two valid items fail on the Registry; the invalid one on its validation.
    assert [r["error_code"] for r in results] == ["registry_error", "validation_failed", "registry_error"]


@pytest.mark.asyncio
async def test_one_registry_error_fails_only_its_item(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    original = RegistryClient.generate_document_ids_bulk

    async def one_error(self, items, **kwargs):
        results = await original(self, items, **kwargs)
        results[1] = {"status": "error", "error": "refused by the registry"}
        return results

    monkeypatch.setattr(RegistryClient, "generate_document_ids_bulk", one_error)
    results = await _post(client, auth_headers, [
        _person("116100011"), _person("116100012"), _person("116100013"),
    ])
    assert [r["status"] for r in results] == ["created", "error", "created"]


@pytest.mark.asyncio
async def test_an_unreachable_placement_lookup_falls_back_per_item(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    async def unavailable(*_args, **_kwargs):
        raise RegistryUnavailableError("registry unreachable")

    monkeypatch.setattr(ReferenceValidator, "place_references", unavailable)
    results = await _post(client, auth_headers, [_person("116100021"), _person("116100022")])
    assert [r["status"] for r in results] == ["created", "created"]
