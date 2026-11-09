"""A dependency that cannot answer refuses the write; it is never read as
"not found", a warning, or a pass.

document-store validates a write against def-store (terms, terminologies)
and the template store (the document's template, nested and array-item
templates, referenced documents' templates, template references). Each of
these used to read an outage as an answer: a term or template "not found", a
nested template skipped with a warning, an endpoint check skipped, an
unexpected resolution failure downgraded to a warning that let the write
through. Now:

- The clients mark an outage (transport error, or an answer that is neither
  200, 404 nor the store's own refusal) as ``unavailable``.
- Every site that gates the write turns such an error into
  DependencyUnavailableError: per item ``dependency_unavailable`` (details
  name the dependency), 503 where a request has no items.
- What the store genuinely answers is still reported as such: a template
  that is not there is template_not_found, a term that does not match is
  invalid_term_reference.
- An ambiguous declared endpoint is reported as ambiguous_reference instead
  of dropped (which refused a legal reference); any other failure while
  checking a reference is an error, never a warning.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from document_store.services import validation_service as vs
from document_store.services.def_store_client import DefStoreClient, DefStoreError
from document_store.services.template_store_client import (
    TemplateStoreClient,
    TemplateStoreError,
)
from document_store.services.validation_service import ValidationResult, ValidationService
from wip_auth.registry_client import AmbiguousIdentifierError, DependencyUnavailableError

DOWN_DEF = DefStoreError("Request failed: refused", unavailable=True)
DOWN_TPL = TemplateStoreError("Request failed: refused", unavailable=True)


# -- the clients mark outages --------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 503, 401])
async def test_the_template_client_marks_an_error_status_unavailable(httpx_mock, status):
    httpx_mock.add_response(status_code=status, text="no")
    with pytest.raises(TemplateStoreError) as info:
        await TemplateStoreClient(base_url="http://ts.test", api_key="k").get_template(
            template_id="T1",
        )
    assert info.value.unavailable


@pytest.mark.asyncio
async def test_the_template_client_marks_a_transport_error_unavailable(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(TemplateStoreError) as info:
        await TemplateStoreClient(base_url="http://ts.test", api_key="k").get_template(
            template_id="T1",
        )
    assert info.value.unavailable


@pytest.mark.asyncio
async def test_the_template_client_answers_404_as_none_and_409_as_a_refusal(httpx_mock):
    client = TemplateStoreClient(base_url="http://ts.test", api_key="k")
    httpx_mock.add_response(status_code=404, json={"detail": "nope"})
    assert await client.get_template(template_id="T1") is None
    httpx_mock.add_response(status_code=409, json={"detail": "broken", "error_code": "inheritance_unresolved"})
    with pytest.raises(TemplateStoreError) as info:
        await client.get_template(template_id="T1")
    assert not info.value.unavailable


@pytest.mark.asyncio
async def test_the_def_store_client_marks_an_outage_unavailable(httpx_mock):
    client = DefStoreClient(base_url="http://ds.test", api_key="k")
    httpx_mock.add_response(status_code=503, text="down")
    with pytest.raises(DefStoreError) as info:
        await client.get_terminology("GENDER", "ns")
    assert info.value.unavailable
    httpx_mock.add_response(status_code=404, json={"detail": "nope"})
    assert await client.get_terminology("GENDER", "ns") is None


# -- fakes ---------------------------------------------------------------------


class _Store:
    """A client fake: every lookup gives `answer` (raised when an error)."""

    def __init__(self, answer: Any):
        self.answer = answer

    async def _give(self, *args, **kwargs):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    get_template = get_template_resolved = get_terminology = validate_value = _give


def _store(monkeypatch, *, defs: Any = None, templates: Any = None) -> None:
    if defs is not None:
        monkeypatch.setattr(vs, "get_def_store_client", lambda: _Store(defs))
    if templates is not None:
        monkeypatch.setattr(vs, "get_template_store_client", lambda: _Store(templates))


# -- term, terminology and template references ---------------------------------


@pytest.mark.asyncio
async def test_a_term_reference_meeting_a_def_store_outage_refuses_the_write(monkeypatch):
    _store(monkeypatch, defs=DOWN_DEF)
    with pytest.raises(DependencyUnavailableError) as info:
        await ValidationService()._resolve_term_reference("M", ["GENDER"], ValidationResult(), "g", "ns")
    assert info.value.dependency == "def-store"


@pytest.mark.asyncio
async def test_a_term_def_store_refuses_to_resolve_is_still_a_miss(monkeypatch):
    _store(monkeypatch, defs=DefStoreError("no such terminology"))
    result = ValidationResult()
    assert await ValidationService()._resolve_term_reference("M", ["GENDER"], result, "g", "ns") is None
    assert [e["code"] for e in result.errors] == ["invalid_term_reference"]


@pytest.mark.asyncio
async def test_a_terminology_reference_meeting_an_outage_refuses_the_write(monkeypatch):
    _store(monkeypatch, defs=DOWN_DEF)
    with pytest.raises(DependencyUnavailableError):
        await ValidationService()._resolve_terminology_reference("GENDER", ValidationResult(), "t", "ns")


@pytest.mark.asyncio
async def test_a_template_reference_meeting_an_outage_refuses_the_write(monkeypatch):
    _store(monkeypatch, templates=DOWN_TPL)
    with pytest.raises(DependencyUnavailableError) as info:
        await ValidationService()._resolve_template_reference("PERSON", ValidationResult(), "t")
    assert info.value.dependency == "template-store"


# -- nested and array-item templates -------------------------------------------


@pytest.mark.asyncio
async def test_a_nested_template_meeting_an_outage_refuses_the_write(monkeypatch):
    _store(monkeypatch, templates=DOWN_TPL)
    with pytest.raises(DependencyUnavailableError):
        await ValidationService()._sub_template("ADDRESS", 1, "home", ValidationResult())


@pytest.mark.asyncio
async def test_a_nested_template_that_is_not_there_is_an_error_not_a_skip(monkeypatch):
    _store(monkeypatch, templates=None)
    monkeypatch.setattr(vs, "get_template_store_client", lambda: _Store(None))
    result = ValidationResult()
    assert await ValidationService()._sub_template("ADDRESS", 1, "home", result) is None
    assert [(e["code"], e["field"]) for e in result.errors] == [("template_not_found", "home")]


@pytest.mark.asyncio
async def test_a_nested_template_the_store_refuses_is_an_error(monkeypatch):
    _store(monkeypatch, templates=TemplateStoreError("broken chain", code="inheritance_unresolved"))
    result = ValidationResult()
    assert await ValidationService()._sub_template("ADDRESS", 1, "home", result) is None
    assert [e["code"] for e in result.errors] == ["template_unresolvable"]


# -- the referenced document's template and its endpoints ----------------------


class _Doc:
    document_id = "d1"
    identity_hash = "h"
    template_id = "T1"
    version = 1
    namespace = "ns"

    class status:  # noqa: N801 — mirrors DocumentStatus.ACTIVE's shape
        value = "active"


@pytest.fixture
def active_doc(monkeypatch):
    from document_store.models.document import DocumentStatus

    doc = _Doc()
    doc.status = DocumentStatus.ACTIVE
    return doc


@pytest.mark.asyncio
async def test_the_endpoint_check_is_never_skipped_for_an_outage(monkeypatch, active_doc):
    _store(monkeypatch, templates=DOWN_TPL)
    with pytest.raises(DependencyUnavailableError):
        await ValidationService()._verify_ref_template_and_build_result(
            active_doc, ["THING"], ValidationResult(), "ref", namespace="ns",
        )


@pytest.mark.asyncio
async def test_the_endpoint_check_runs_when_the_template_is_not_found(monkeypatch, active_doc):
    _store(monkeypatch, templates=None)
    monkeypatch.setattr(vs, "get_template_store_client", lambda: _Store(None))

    async def allowed(self, target_templates, namespace):
        return {"OTHER"}

    monkeypatch.setattr(ValidationService, "_allowed_template_ids", allowed)
    result = ValidationResult()
    out = await ValidationService()._verify_ref_template_and_build_result(
        active_doc, ["THING"], result, "ref", namespace="ns",
    )
    assert out is None
    assert [e["code"] for e in result.errors] == ["invalid_reference_template"]
    assert not result.warnings


@pytest.mark.asyncio
async def test_an_ambiguous_endpoint_is_reported_not_dropped(monkeypatch, active_doc):
    monkeypatch.setattr(vs, "get_template_store_client", lambda: _Store({"value": "THING"}))

    async def ambiguous(self, target_templates, namespace):
        raise AmbiguousIdentifierError("THING", [{"entry_id": "a"}, {"entry_id": "b"}], 2)

    monkeypatch.setattr(ValidationService, "_allowed_template_ids", ambiguous)
    result = ValidationResult()
    out = await ValidationService()._verify_ref_template_and_build_result(
        active_doc, ["THING"], result, "ref", namespace="ns",
    )
    assert out is None
    assert [e["code"] for e in result.errors] == ["ambiguous_reference"]


@pytest.mark.asyncio
async def test_allowed_template_ids_lets_ambiguity_through(monkeypatch):
    from document_store.services import registry_client as rc

    class _Registry:
        async def resolve_identifier(self, namespace, entity_type, value):
            raise AmbiguousIdentifierError(value, [], 2)

    monkeypatch.setattr(rc, "get_registry_client", lambda: _Registry())
    with pytest.raises(AmbiguousIdentifierError):
        await ValidationService()._allowed_template_ids(["THING"], "ns")


# -- any other failure while checking a reference ------------------------------


@pytest.mark.asyncio
async def test_a_reference_check_that_crashes_is_an_error_not_a_warning(monkeypatch):
    async def crash(self, value, result, field_path, namespace=None):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(ValidationService, "_resolve_terminology_reference", crash)
    template = {"fields": [{"name": "t", "type": "reference", "reference_type": "terminology"}]}
    result = ValidationResult()
    await ValidationService()._validate_references({"t": "GENDER"}, template, result, "ns")
    assert [e["code"] for e in result.errors] == ["reference_check_failed"]
    assert not result.warnings


@pytest.mark.asyncio
async def test_a_dependency_outage_while_checking_a_reference_is_raised(monkeypatch):
    async def down(self, value, result, field_path, namespace=None):
        raise DependencyUnavailableError("def-store unavailable", "def-store")

    monkeypatch.setattr(ValidationService, "_resolve_terminology_reference", down)
    template = {"fields": [{"name": "t", "type": "reference", "reference_type": "terminology"}]}
    with pytest.raises(DependencyUnavailableError):
        await ValidationService()._validate_references({"t": "GENDER"}, template, ValidationResult(), "ns")


# -- through the API: every write path reports the outage ----------------------


DOCS = "/api/document-store/documents"


def _outage(monkeypatch) -> None:
    async def down(self, *args, **kwargs):
        raise DependencyUnavailableError("def-store unavailable (term): HTTP 503", "def-store")

    from document_store.services.document_service import DocumentService

    monkeypatch.setattr(DocumentService, "_validate_as_writer", down)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2], ids=["single_path", "bulk_path"])
async def test_a_create_meeting_an_outage_is_dependency_unavailable(
    client: AsyncClient, auth_headers: dict, monkeypatch, count: int,
):
    _outage(monkeypatch)
    item = {"template_id": "CHAPTER_Q", "namespace": "wip", "data": {"title": "Outage 1139"}}
    response = await client.post(DOCS, headers=auth_headers, json=[item] * count)
    assert response.status_code == 200, response.text
    first = sorted(response.json()["results"], key=lambda r: r["index"])[0]
    assert first["error_code"] == "dependency_unavailable", first
    assert first["details"] == {"dependency": "def-store"}


@pytest.mark.asyncio
async def test_a_patch_meeting_an_outage_is_dependency_unavailable_not_internal(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    created = await client.post(DOCS, headers=auth_headers, json=[
        {"template_id": "CHAPTER_Q", "namespace": "wip", "data": {"title": "Patch 1139"}},
    ])
    document_id = created.json()["results"][0]["document_id"]
    _outage(monkeypatch)
    response = await client.patch(DOCS, headers=auth_headers, json=[
        {"document_id": document_id, "patch": {"summary": "x"}},
    ])
    assert response.status_code == 200, response.text
    item = response.json()["results"][0]
    assert item["error_code"] == "dependency_unavailable", item
