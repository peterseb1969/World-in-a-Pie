"""CASE-1279 reopen (FRanC forward review of ec7ebb81, e21a20b6).

- A versioned write retires its predecessor only as it read it: a soft
  delete that lands first stands (single POST, PATCH, bulk create).
- A delete that loses to a successor version retires the successor.
- A candidate's parent and nested templates pass the inbound gate.
"""

import pytest

from document_store.models.document import Document
from document_store.services.document_service import DocumentService

API = "/api/document-store"


def _person(nid: str, first: str = "A") -> dict:
    return {"namespace": "wip", "template_id": "PERSON",
            "data": {"national_id": nid, "first_name": first, "last_name": "R"}}


async def _post(client, headers, items):
    resp = await client.post(f"{API}/documents", headers=headers, json=items)
    assert resp.status_code == 200, resp.text
    return resp.json()["results"]


async def _delete(client, headers, doc_id):
    resp = await client.request("DELETE", f"{API}/documents", headers=headers, json=[{"id": doc_id}])
    assert resp.status_code == 200, resp.text
    return resp.json()["results"][0]


async def _rows(doc_id):
    return sorted((d.version, d.status.value) for d in await Document.find({"document_id": doc_id}).to_list())


@pytest.mark.asyncio
async def test_a_delete_before_a_single_versioned_write_stands(client, auth_headers, monkeypatch):
    [created] = await _post(client, auth_headers, [_person("127910001")])
    doc_id = created["document_id"]
    original = DocumentService._is_template_versioned
    fired = {"done": False}

    async def delete_first(self, *args, **kwargs):
        if not fired["done"]:
            fired["done"] = True
            assert (await _delete(client, auth_headers, doc_id))["status"] == "deleted"
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(DocumentService, "_is_template_versioned", delete_first)
    [item] = await _post(client, auth_headers, [_person("127910001", "B")])
    assert item["status"] == "error" and item["error_code"] == "concurrency_conflict", item
    assert await _rows(doc_id) == [(1, "inactive")]


@pytest.mark.asyncio
async def test_a_delete_before_a_versioned_patch_stands(client, auth_headers, monkeypatch):
    [created] = await _post(client, auth_headers, [_person("127910002")])
    doc_id = created["document_id"]
    original = DocumentService._relationship_refusal
    fired = {"done": False}

    async def delete_first(self, *args, **kwargs):
        if not fired["done"]:
            fired["done"] = True
            assert (await _delete(client, auth_headers, doc_id))["status"] == "deleted"
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(DocumentService, "_relationship_refusal", delete_first)
    resp = await client.patch(f"{API}/documents", headers=auth_headers,
                              json=[{"document_id": doc_id, "patch": {"first_name": "B"}}])
    item = resp.json()["results"][0]
    assert item["status"] == "error" and item["error_code"] == "not_found", item
    assert await _rows(doc_id) == [(1, "inactive")]


@pytest.mark.asyncio
async def test_a_delete_before_a_bulk_write_stands_for_that_item(client, auth_headers, monkeypatch):
    [created] = await _post(client, auth_headers, [_person("127910003")])
    doc_id = created["document_id"]
    original = DocumentService._write_planned

    async def delete_first(self, *args, **kwargs):
        assert (await _delete(client, auth_headers, doc_id))["status"] == "deleted"
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(DocumentService, "_write_planned", delete_first)
    results = await _post(client, auth_headers, [_person("127910003", "B"), _person("127910004")])
    by_index = {r["index"]: r for r in results}
    assert by_index[0]["error_code"] == "concurrency_conflict", by_index[0]
    assert by_index[1]["status"] == "created", by_index[1]
    assert await _rows(doc_id) == [(1, "inactive")]


@pytest.mark.asyncio
async def test_a_delete_that_loses_to_a_successor_retires_it(client, auth_headers, monkeypatch):
    [created] = await _post(client, auth_headers, [_person("127910005")])
    doc_id = created["document_id"]
    original = DocumentService._record_lifecycle
    fired = {"done": False}

    async def successor_first(*args, **kwargs):  # a staticmethod
        if not fired["done"]:
            fired["done"] = True
            [v2] = await _post(client, auth_headers, [_person("127910005", "B")])
            assert v2["version"] == 2, v2
        return await original(*args, **kwargs)

    monkeypatch.setattr(DocumentService, "_record_lifecycle", staticmethod(successor_first))
    assert (await _delete(client, auth_headers, doc_id))["status"] == "deleted"
    assert await _rows(doc_id) == [(1, "inactive"), (2, "inactive")]


@pytest.mark.asyncio
async def test_a_candidate_cannot_read_a_gated_parent_or_nested_template(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from document_store.services import validation_service as vs

    store = MagicMock()
    store.get_template_resolved = AsyncMock(return_value={
        "template_id": "CLOSED-T", "namespace": "closed",
        "fields": [{"name": "secret_field", "type": "string"}],
    })
    monkeypatch.setattr(vs, "get_template_store_client", lambda: store)

    async def refuses(namespace, entity_id):
        return namespace == "closed"

    monkeypatch.setattr(vs, "gate_refuses", refuses)
    service = vs.ValidationService()
    for candidate in (
        {"extends": "CLOSED-T", "extends_version": 1, "fields": []},
        {"fields": [{"name": "o", "type": "object", "template_ref": "CLOSED-T",
                     "template_ref_version": 1}]},
    ):
        result = vs.ValidationResult()
        assert await service._composed_candidate(dict(candidate), result) is None
        assert [e["code"] for e in result.errors] == ["template_not_found"]
        assert "secret_field" not in str(result.errors)


@pytest.mark.asyncio
async def test_a_candidate_parent_without_a_pinned_version_is_refused():
    from document_store.services import validation_service as vs

    result = vs.ValidationResult()
    assert await vs.ValidationService()._composed_candidate(
        {"extends": "P", "fields": []}, result,
    ) is None
    assert "extends_version" in result.errors[0]["message"]
