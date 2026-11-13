"""A template-store outage on the bulk paths, and a target family that cannot
be expanded or is ambiguous, are reported as such.

Provenance: CASE-1264 (commit-review backfill, findings on 31b74b32 and
ae22e496). Bulk create guarded only its Stage 0 warm-up: a template read at
Stage 1b (the relationship and isolation gates) or Stage 2 (the Registry
items) still escaped as a 500. Stage 2 now reads the template's usage from
validation, and Stage 1b refuses the item (dependency_unavailable).
validate-bulk shares the guarded warm-up. include_subtypes read an outage as
"no subtypes" and cached it; it now refuses. A target_templates entry naming
several templates is ambiguous_reference on every path, not only the
endpoint check.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from document_store.services import document_service as ds
from document_store.services import validation_service as vs
from document_store.services.template_store_client import TemplateStoreError
from document_store.services.validation_service import ValidationResult, ValidationService
from wip_auth.registry_client import AmbiguousIdentifierError, DependencyUnavailableError

DOCS = "/api/document-store/documents"
DOWN = TemplateStoreError("Request failed: refused", unavailable=True)


class _Down:
    """A template-store client whose every read fails as an outage."""

    async def _down(self, *args: Any, **kwargs: Any) -> Any:
        raise DOWN

    get_template = get_template_resolved = get_template_descendants = _down


def _items(n: int, tag: str) -> list[dict[str, Any]]:
    return [
        {"template_id": "CHAPTER_Q", "namespace": "wip", "data": {"title": f"{tag} {i}"}}
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_a_bulk_create_whose_gate_meets_an_outage_refuses_each_item(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    # Validation reads its own client (served); the bulk path's warm-up and
    # Stage 1b gate read document_service's, which is down.
    monkeypatch.setattr(ds, "get_template_store_client", lambda: _Down())
    response = await client.post(DOCS, headers=auth_headers, json=_items(2, "Gate 1264"))
    assert response.status_code == 200, response.text
    results = sorted(response.json()["results"], key=lambda r: r["index"])
    assert [r["error_code"] for r in results] == ["dependency_unavailable"] * 2, results
    assert results[0]["details"] == {"dependency": "template-store"}


@pytest.mark.asyncio
async def test_a_bulk_create_meeting_an_outage_everywhere_refuses_each_item(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    # No stage is patched out: the real validation meets the outage.
    monkeypatch.setattr(ds, "get_template_store_client", lambda: _Down())
    monkeypatch.setattr(vs, "get_template_store_client", lambda: _Down())
    response = await client.post(DOCS, headers=auth_headers, json=_items(2, "Down 1264"))
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [r["error_code"] for r in results] == ["dependency_unavailable"] * 2, results


def test_the_registry_items_read_usage_from_validation():
    # Stage 2 asks template-store nothing: usage is immutable and validation
    # already read it.
    class _Item:
        template_id = "T1"
        document_id = None
        version = None

    edge, entity = ValidationResult(), ValidationResult()
    edge.template_usage, entity.template_usage = "relationship", "entity"
    items = ds.DocumentService._registry_items([(0, _Item(), edge), (1, _Item(), entity)])
    assert [i["skip_identity_value_synonym"] for i in items] == [True, False]


@pytest.mark.asyncio
async def test_validate_bulk_survives_a_warm_up_outage(
    client: AsyncClient, auth_headers: dict, monkeypatch,
):
    monkeypatch.setattr(ds, "get_template_store_client", lambda: _Down())
    response = await client.post(
        "/api/document-store/validation/validate-bulk",
        headers=auth_headers,
        json={"template_id": "CHAPTER_Q", "namespace": "wip", "items": [{"title": "a"}, {"title": "b"}]},
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_subtypes_meeting_an_outage_refuse_and_are_not_cached(monkeypatch):
    monkeypatch.setattr(vs, "get_template_store_client", lambda: _Down())
    cache: dict[str, list[str]] = {}
    with pytest.raises(DependencyUnavailableError) as info:
        await ValidationService()._expand_target_templates(["PARENT"], cache)
    assert info.value.dependency == "template-store"
    assert cache == {}


@pytest.mark.asyncio
async def test_a_template_the_store_does_not_serve_has_no_subtypes(monkeypatch):
    class _Refuses(_Down):
        async def _refuse(self, *args: Any, **kwargs: Any) -> Any:
            raise TemplateStoreError("not served", code="inheritance_unresolved")

        get_template = _refuse

    monkeypatch.setattr(vs, "get_template_store_client", lambda: _Refuses())
    cache: dict[str, list[str]] = {}
    assert await ValidationService()._expand_target_templates(["PARENT"], cache) == ["PARENT"]
    assert cache == {"PARENT": []}


@pytest.mark.asyncio
async def test_an_ambiguous_target_family_is_ambiguous_on_the_value_path(monkeypatch):
    async def ambiguous(self, *args: Any, **kwargs: Any) -> Any:
        # As _allowed_template_ids raises it while narrowing a bare value or
        # matching a business key.
        raise AmbiguousIdentifierError("THING", [{"entry_id": "a"}, {"entry_id": "b"}], 2)

    monkeypatch.setattr(ValidationService, "_resolve_document_reference", ambiguous)
    template = {"fields": [{
        "name": "ref", "type": "reference", "reference_type": "document",
        "target_templates": ["THING"],
    }]}
    result = ValidationResult()
    await ValidationService()._validate_references({"ref": "some title"}, template, result, "ns")
    assert [(e["code"], e["field"]) for e in result.errors] == [("ambiguous_reference", "ref")]
    assert result.errors[0]["details"]["value"] == "THING"
