"""The field-form term lookup accepts the terminology in every form a
terminology can be named (CASE-914).

A term's Registry synonym records its terminology by VALUE, in the
terminology's own namespace. The field-form resolver used to copy the
caller's terminology string into the key unchanged, so only the bare value
could match: the canonical id and NS:VALUE answered "not found" for terms
that exist, and the id still demanded a namespace it does not need.

These tests stand a small Registry in for the real one: one terminology
COLOR in namespace `paint` (id TERMINOLOGY_ID), one term `red` under it.
"""

import json

import httpx
import pytest
from fastapi import HTTPException

from wip_auth.fastapi_helpers import resolve_term_by_fields_or_404
from wip_auth.resolve import (
    EntityNotFoundError,
    TermNamespaceRequiredError,
    clear_resolution_cache,
    resolve_term_by_fields,
    terminology_coordinates_by_id,
)

TERMINOLOGY_ID = "019a0000-0000-7000-8000-00000000c010"
PREFIXED_ID = "LOV-000042"
TERM_ID = "019a0000-0000-7000-8000-0000000000ed"
RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"
BY_ID_URL = "http://localhost:8001/api/registry/entries/lookup/by-id"

# The term's synonym, exactly as def-store registers it.
TERM_KEY = {"ns": "paint", "type": "term", "terminology": "COLOR", "value": "red"}


@pytest.fixture(autouse=True)
def clean_cache():
    clear_resolution_cache()
    yield
    clear_resolution_cache()


def _registry(httpx_mock, *, known_ids=(TERMINOLOGY_ID,)):
    """Install the stand-in Registry; returns the list of requests it saw."""
    seen: list[tuple[str, dict]] = []

    def resolve(request: httpx.Request) -> httpx.Response:
        item = json.loads(request.content)[0]
        seen.append(("resolve", item))
        found = item.get("composite_key") == TERM_KEY
        result = {"status": "found", "entry_id": TERM_ID} if found else {"status": "not_found"}
        return httpx.Response(200, json={"results": [result]})

    def by_id(request: httpx.Request) -> httpx.Response:
        item = json.loads(request.content)[0]
        seen.append(("by-id", item))
        if item["entry_id"] in known_ids:
            result = {
                "status": "found",
                "entry_id": item["entry_id"],
                "namespace": "paint",
                "entity_type": "terminologies",
                "matched_via": "entry_id",
                "matched_composite_key": {"ns": "paint", "value": "COLOR", "label": "Color"},
                "synonyms": [],
            }
        else:
            result = {"status": "not_found"}
        return httpx.Response(200, json={"results": [result]})

    httpx_mock.add_callback(resolve, url=RESOLVE_URL, is_optional=True, is_reusable=True)
    httpx_mock.add_callback(by_id, url=BY_ID_URL, is_optional=True, is_reusable=True)
    return seen


class TestEveryTerminologyForm:
    @pytest.mark.asyncio
    async def test_bare_value_in_namespace(self, httpx_mock):
        seen = _registry(httpx_mock)
        assert await resolve_term_by_fields("red", "COLOR", "paint") == TERM_ID
        assert [kind for kind, _ in seen] == ["resolve"]  # the common case stays one call

    @pytest.mark.asyncio
    async def test_canonical_id_needs_no_namespace(self, httpx_mock):
        _registry(httpx_mock)
        assert await resolve_term_by_fields("red", TERMINOLOGY_ID, None) == TERM_ID

    @pytest.mark.asyncio
    async def test_canonical_id_with_a_namespace(self, httpx_mock):
        _registry(httpx_mock)
        assert await resolve_term_by_fields("red", TERMINOLOGY_ID, "paint") == TERM_ID

    @pytest.mark.asyncio
    async def test_qualified_value_names_its_namespace(self, httpx_mock):
        _registry(httpx_mock)
        assert await resolve_term_by_fields("red", "paint:COLOR", None) == TERM_ID
        # From another namespace, too: the qualifier wins over the request's.
        clear_resolution_cache()
        assert await resolve_term_by_fields("red", "paint:COLOR", "elsewhere") == TERM_ID

    @pytest.mark.asyncio
    async def test_prefixed_canonical_id_resolves_after_the_value_miss(self, httpx_mock):
        seen = _registry(httpx_mock, known_ids=(PREFIXED_ID,))
        assert await resolve_term_by_fields("red", PREFIXED_ID, "paint") == TERM_ID
        assert [kind for kind, _ in seen] == ["resolve", "by-id", "resolve"]


class TestTermValueStaysOpaque:
    @pytest.mark.asyncio
    async def test_colon_term_value_is_never_split(self, httpx_mock):
        seen = _registry(httpx_mock)
        with pytest.raises(EntityNotFoundError):
            await resolve_term_by_fields("GO:0000278", TERMINOLOGY_ID, None)
        sent = [item["composite_key"] for kind, item in seen if kind == "resolve"]
        assert sent == [{"ns": "paint", "type": "term", "terminology": "COLOR", "value": "GO:0000278"}]

    @pytest.mark.asyncio
    async def test_colon_carrying_terminology_value_still_resolves_literally(self, httpx_mock):
        """A terminology whose VALUE contains ':' was reachable before; the
        qualified reading is tried first, the literal value after it."""
        seen: list[dict] = []

        def resolve(request):
            item = json.loads(request.content)[0]
            seen.append(item["composite_key"])
            hit = item["composite_key"] == {"ns": "onto", "type": "term", "terminology": "GO:SLIM", "value": "x"}
            body = {"status": "found", "entry_id": TERM_ID} if hit else {"status": "not_found"}
            return httpx.Response(200, json={"results": [body]})

        httpx_mock.add_callback(resolve, url=RESOLVE_URL, is_reusable=True)
        assert await resolve_term_by_fields("x", "GO:SLIM", "onto") == TERM_ID
        assert [k["ns"] for k in seen] == ["GO", "onto"]


class TestMisses:
    @pytest.mark.asyncio
    async def test_unknown_id_is_not_found(self, httpx_mock):
        _registry(httpx_mock)
        with pytest.raises(EntityNotFoundError):
            await resolve_term_by_fields("red", "019a0000-0000-7000-8000-00000000dead", None)

    @pytest.mark.asyncio
    async def test_bare_value_without_namespace_is_a_namespace_error(self, httpx_mock):
        _registry(httpx_mock)
        with pytest.raises(TermNamespaceRequiredError):
            await resolve_term_by_fields("red", "COLOR", None)

    @pytest.mark.asyncio
    async def test_the_door_answers_422_for_a_bare_value_without_namespace(self, httpx_mock):
        _registry(httpx_mock)
        with pytest.raises(HTTPException) as exc:
            await resolve_term_by_fields_or_404("red", "COLOR", None, param_name="term_id")
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_the_door_answers_404_for_a_missing_term(self, httpx_mock):
        _registry(httpx_mock)
        with pytest.raises(HTTPException) as exc:
            await resolve_term_by_fields_or_404("blue", TERMINOLOGY_ID, None, param_name="term_id")
        assert exc.value.status_code == 404


class TestTerminologyCoordinates:
    @pytest.mark.asyncio
    async def test_reads_namespace_and_value_from_the_entry(self, httpx_mock):
        _registry(httpx_mock)
        assert await terminology_coordinates_by_id(TERMINOLOGY_ID) == ("paint", "COLOR")

    @pytest.mark.asyncio
    async def test_a_search_values_match_is_not_an_id(self, httpx_mock):
        """The lookup's fallback matches bare values in any namespace; only
        an exact entry_id match may count."""
        def by_id(request):
            return httpx.Response(200, json={"results": [{
                "status": "found", "entry_id": TERMINOLOGY_ID, "namespace": "paint",
                "matched_via": "composite_key_value",
                "matched_composite_key": {"ns": "paint", "value": "COLOR"},
            }]})

        httpx_mock.add_callback(by_id, url=BY_ID_URL)
        assert await terminology_coordinates_by_id("COLOR") is None

    @pytest.mark.asyncio
    async def test_value_from_the_synonym_when_the_primary_key_lacks_it(self, httpx_mock):
        def by_id(request):
            return httpx.Response(200, json={"results": [{
                "status": "found", "entry_id": TERMINOLOGY_ID, "namespace": "paint",
                "matched_via": "entry_id", "matched_composite_key": {"label": "Color"},
                "synonyms": [{"composite_key": {"ns": "paint", "type": "terminology", "value": "COLOR"}}],
            }]})

        httpx_mock.add_callback(by_id, url=BY_ID_URL)
        assert await terminology_coordinates_by_id(TERMINOLOGY_ID) == ("paint", "COLOR")
