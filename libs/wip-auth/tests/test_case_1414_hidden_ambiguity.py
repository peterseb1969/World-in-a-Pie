"""An ambiguity among entities the caller cannot read answers as a miss.

Provenance: CASE-1414 (the class #3 asked to enumerate). An identifier several
entities bear is answered as ambiguous, naming the candidates: their canonical
ids, the keys that carried the value, their namespaces. Resolved by a prefix
naming a namespace the caller cannot read, that answer confirmed the entities
exist and gave them away, while a value nothing bears answers as a miss. Every
door helper now drops the candidates the caller cannot read and answers an
ambiguity left with none as the miss.

Also pins SubmittedIds: a bulk error result names the ids as submitted.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from wip_auth.bulk_models import TemplateBulkResponse as BulkResponse
from wip_auth.bulk_models import TemplateBulkResultItem as BulkResultItem
from wip_auth.fastapi_helpers import (
    SubmittedIds,
    resolve_bulk_ids,
    resolve_or_404,
    resolve_term_by_fields_or_404,
)
from wip_auth.identity import reset_current_identity, set_current_identity
from wip_auth.models import UserIdentity
from wip_auth.resolve import clear_resolution_cache

RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"
ONE = "019a0000-0000-7000-8000-000000000001"
TWO = "019a0000-0000-7000-8000-000000000002"
THREE = "019a0000-0000-7000-8000-000000000003"
FOUR = "019a0000-0000-7000-8000-000000000004"


def _candidates(*namespaces: str) -> list[dict]:
    return [
        {"entry_id": entry_id, "namespace": ns, "entity_type": "templates", "matched_via": "synonym"}
        for entry_id, ns in zip((ONE, TWO), namespaces, strict=False)
    ]


@pytest.fixture(autouse=True)
def caller(monkeypatch):
    """An authenticated caller who can read 'open' and nothing else."""
    monkeypatch.setenv("REGISTRY_API_KEY", "test-key")
    clear_resolution_cache()

    async def permission(_identity, namespace):
        return "read" if namespace == "open" else "none"

    token = set_current_identity(UserIdentity(user_id="u", username="u", auth_method="api_key"))
    with patch("wip_auth.permissions.resolve_permission", side_effect=permission):
        yield
    reset_current_identity(token)
    clear_resolution_cache()


def _answer(httpx_mock, *namespaces: str) -> None:
    httpx_mock.add_response(url=RESOLVE_URL, is_reusable=True, json={"results": [
        {"status": "ambiguous", "entry_id": None, "candidates": _candidates(*namespaces)},
    ]})


@pytest.mark.asyncio
async def test_a_single_door_answers_a_hidden_ambiguity_as_a_miss(httpx_mock):
    _answer(httpx_mock, "hidden", "hidden")
    with pytest.raises(HTTPException) as caught:
        await resolve_or_404("COLOR", "template", "hidden")
    assert caught.value.status_code == 404
    assert ONE not in str(caught.value.detail) and TWO not in str(caught.value.detail)


@pytest.mark.asyncio
async def test_a_single_door_keeps_a_visible_ambiguity(httpx_mock):
    _answer(httpx_mock, "open", "open")
    with pytest.raises(HTTPException) as caught:
        await resolve_or_404("COLOR", "template", "open")
    assert caught.value.status_code == 409
    assert caught.value.detail["total"] == 2


@pytest.mark.asyncio
async def test_a_mixed_ambiguity_names_only_the_readable_candidates(httpx_mock):
    _answer(httpx_mock, "open", "hidden")
    with pytest.raises(HTTPException) as caught:
        await resolve_or_404("COLOR", "template", "open")
    assert caught.value.status_code == 409
    assert [c["entry_id"] for c in caught.value.detail["candidates"]] == [ONE]
    assert TWO not in str(caught.value.detail)


@pytest.mark.asyncio
async def test_a_qualified_hidden_ambiguity_passes_through_as_submitted(httpx_mock):
    _answer(httpx_mock, "hidden", "hidden")
    # No namespace and no key to derive one: the prefix names the namespace,
    # and a miss there returns the identifier as submitted.
    with patch("wip_auth.fastapi_helpers._derive_namespace_from_identity", return_value=None):
        assert await resolve_or_404("hidden:COLOR", "template", None) == "hidden:COLOR"


@pytest.mark.asyncio
async def test_the_field_form_door_answers_a_hidden_ambiguity_as_a_miss(httpx_mock):
    _answer(httpx_mock, "hidden", "hidden")
    with pytest.raises(HTTPException) as caught:
        await resolve_term_by_fields_or_404("red", "hidden:COLORS", "hidden")
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_the_bulk_string_door_leaves_a_hidden_ambiguity_unresolved(httpx_mock):
    _answer(httpx_mock, "hidden", "hidden")
    items = [{"template_id": "COLOR"}]
    assert await resolve_bulk_ids(items, "template_id", "template", "hidden", bypass_cache=True) == {}
    assert items == [{"template_id": "COLOR"}]


@pytest.mark.asyncio
async def test_the_bulk_field_door_leaves_a_hidden_ambiguity_unresolved(httpx_mock):
    _answer(httpx_mock, "hidden", "hidden")
    items = [SimpleNamespace(source_term_id="red", source_terminology="hidden:COLORS")]
    ambiguous = await resolve_bulk_ids(
        items, "source_term_id", "term", "hidden",
        terminology_field="source_terminology", bypass_cache=True,
    )
    assert ambiguous == {}
    assert items[0].source_term_id == "red"


@pytest.mark.asyncio
async def test_the_bulk_doors_keep_a_visible_ambiguity(httpx_mock):
    _answer(httpx_mock, "open", "open")
    items = [{"template_id": "COLOR"}]
    assert list(await resolve_bulk_ids(items, "template_id", "template", "open", bypass_cache=True)) == [0]


def test_every_error_result_names_the_ids_as_submitted():
    items = [
        {"template_id": "hidden:COLOR", "parent": None},
        {"template_id": "open:SHAPE", "parent": "hidden:BASE"},
        {"template_id": "open:SIZE", "parent": None},
    ]
    submitted = SubmittedIds(items, "template_id", "parent")
    # Resolved in place, as resolve_bulk_ids does.
    items[0]["template_id"], items[1]["template_id"], items[1]["parent"] = ONE, TWO, THREE
    items[2]["template_id"] = FOUR
    response = BulkResponse(results=[
        BulkResultItem(index=0, status="error", id=ONE, error=f"Template {ONE} not found", error_code="not_found"),
        BulkResultItem(index=1, status="error", id=TWO, error=f"parent '{THREE}' names nothing",
                       error_code="reference_violation"),
        BulkResultItem(index=2, status="updated", id=FOUR),
    ], total=3, succeeded=1, failed=2)

    rows = submitted.answer(response).results

    assert (rows[0].id, rows[0].error) == ("hidden:COLOR", "Template hidden:COLOR not found")
    assert (rows[1].id, rows[1].error) == ("open:SHAPE", "parent 'hidden:BASE' names nothing")
    assert rows[2].id == FOUR, "an item that was written keeps its canonical id"
