"""A business-key reference fails visibly when the services it needs cannot
answer, never as "not found".

A business-key reference ({identity field: value, ...}) is resolved by
reading each target template from the template store and looking the
document up by its Registry primary key. Two outages used to read as a miss:
a Registry that did not answer the primary-key lookup counted as "no match"
(the write then reported reference_not_found), and a target template the
template store could not answer was skipped (the key then "named no target
template's identity"). A Registry that cannot answer fails the write as
unavailable (RegistryUnavailableError, a 503 / per-item registry_error); a
template store that cannot answer fails the reference with template_error,
as the document's own template does.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from document_store.services import registry_client as rc
from document_store.services import validation_service as vs
from document_store.services.registry_client import RegistryClient
from document_store.services.template_store_client import TemplateStoreError
from document_store.services.validation_service import ValidationService
from wip_auth.registry_client import (
    RegistryUnavailableError,
    clear_registry_transport,
    set_registry_transport,
)

KEY = {"code": "X1"}
TEMPLATE = {
    "template_id": "T1", "value": "THING", "identity_fields": ["code"],
    "fields": [{"name": "code", "type": "string"}],
}


@pytest.fixture
def registry_answers():
    """Point the Registry client at a fake Registry; the test sets the answer."""
    state: dict[str, Any] = {"answer": None}

    def handler(request: httpx.Request) -> httpx.Response:
        answer = state["answer"]
        if isinstance(answer, Exception):
            raise answer
        return answer

    import wip_auth.registry_client as base

    previous = base._registry_transport
    set_registry_transport(httpx.MockTransport(handler))
    yield state
    if previous is None:
        clear_registry_transport()
    else:
        set_registry_transport(previous)


def _client() -> RegistryClient:
    return RegistryClient(base_url="http://registry.test", api_key="k")


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    httpx.Response(503, text="down"),
    httpx.Response(500, text="boom"),
    httpx.ConnectError("refused"),
    httpx.ReadTimeout("slow"),
])
async def test_the_primary_key_lookup_raises_unavailable_when_the_registry_cannot_answer(registry_answers, answer):
    registry_answers["answer"] = answer
    with pytest.raises(RegistryUnavailableError):
        await _client().lookup_document_by_primary_key("ns", "T1", "h")


@pytest.mark.asyncio
async def test_the_primary_key_lookup_still_answers_found_and_not_found(registry_answers):
    registry_answers["answer"] = httpx.Response(200, json={"results": [{"status": "found", "entry_id": "d1"}]})
    assert await _client().lookup_document_by_primary_key("ns", "T1", "h") == "d1"
    registry_answers["answer"] = httpx.Response(200, json={"results": [{"status": "not_found"}]})
    assert await _client().lookup_document_by_primary_key("ns", "T1", "h") is None


class _Templates:
    def __init__(self, answer: Any):
        self.answer = answer

    async def get_template(self, template_id):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def resolving(monkeypatch):
    """The key resolves under T1; only the lookups the test breaks fail."""
    async def terms(self, data, template, result, namespace=None):
        return None

    async def references(self, data, template, result, namespace=None, doc_ref_cache=None):
        return None

    def identity(self, data, template, result):
        result.identity_values = dict(data)

    async def allowed(self, target_templates, namespace):
        return {"T1"}

    monkeypatch.setattr(ValidationService, "_validate_terms", terms)
    monkeypatch.setattr(ValidationService, "_validate_references", references)
    monkeypatch.setattr(ValidationService, "_compute_identity", identity)
    monkeypatch.setattr(ValidationService, "_allowed_template_ids", allowed)


@pytest.mark.asyncio
async def test_a_registry_outage_on_the_key_lookup_fails_the_write(monkeypatch, resolving, registry_answers):
    monkeypatch.setattr(vs, "get_template_store_client", lambda: _Templates(TEMPLATE))
    monkeypatch.setattr(rc, "get_registry_client", _client)
    registry_answers["answer"] = httpx.Response(503, text="down")
    with pytest.raises(RegistryUnavailableError):
        await ValidationService()._resolve_business_key_reference(KEY, ["THING"], "ns", None)


@pytest.mark.asyncio
async def test_a_template_store_that_cannot_answer_fails_the_reference(monkeypatch, resolving):
    monkeypatch.setattr(vs, "get_template_store_client", lambda: _Templates(TemplateStoreError("down")))
    doc, error = await ValidationService()._resolve_business_key_reference(KEY, ["THING"], "ns", None)
    assert doc is None
    assert error is not None and error["code"] == "template_error"
    assert "T1" in error["message"] and "down" in error["message"]


async def _post(client, headers, items):
    payload = [{"namespace": "wip", **item} for item in items]
    return await client.post("/api/document-store/documents", headers=headers, json=payload)


@pytest.mark.asyncio
async def test_a_write_whose_business_key_meets_a_registry_outage_is_not_a_missing_reference(
    client, auth_headers, monkeypatch,
):
    """Through the API: the referent exists, the Registry cannot answer the
    key lookup. The write fails as an outage — the item's error_code is
    registry_error (rule registry_unavailable), alone or in a batch — never
    as reference_not_found."""
    created = await _post(client, auth_headers, [{"template_id": "CHAPTER_Q", "data": {"title": "Outage"}}])
    assert created.status_code == 200, created.text

    async def down(self, namespace, template_id, identity_hash):
        raise RegistryUnavailableError("Registry unavailable (document key lookup): HTTP 503")

    monkeypatch.setattr(RegistryClient, "lookup_document_by_primary_key", down)
    holder = {"template_id": "HOLDER_Q", "data": {"name": "O1", "ref": {"title": "Outage"}}}

    for items in ([holder], [holder, {"template_id": "CHAPTER_Q", "data": {"title": "Other"}}]):
        response = await _post(client, auth_headers, items)
        assert response.status_code == 200, response.text
        first = sorted(response.json()["results"], key=lambda r: r["index"])[0]
        assert first["status"] == "error"
        assert first["error_code"] == "registry_error", first
        assert first["details"] == {"rule": "registry_unavailable"}
        assert "reference_not_found" not in response.text
