"""Regression coverage for CASE-776 — the field-form term resolution door.

A term's identity is the tuple (namespace, terminology, value); the
colon-notation string shorthand is a lossy serialization of it — a VALUE
that itself contains ':' (OBO ids: GO:0000278) cannot be expressed in the
bare or 2-part forms, because no parser can distinguish a structural colon
from a data colon. resolve_term_by_fields builds the structured composite
key directly, so the value is an opaque scalar and never parsed.

Contract under test:

  - resolve_term_by_fields sends the exact composite key tuple; the value
    travels uninterpreted, colons included.
  - Misses raise EntityNotFoundError; results are cached under a
    field-form key distinct from the string-form cache.
  - resolve_or_404's 404 for a colon-carrying bare/2-part term identifier
    names the two lossless doors (fully qualified form / terminology=)
    instead of failing bare.
"""

import json

import pytest
from fastapi import HTTPException

from wip_auth.fastapi_helpers import resolve_or_404
from wip_auth.resolve import (
    EntityNotFoundError,
    clear_resolution_cache,
    resolve_term_by_fields,
)

CANONICAL = "550e8400-e29b-41d4-a716-446655440000"
RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"


@pytest.fixture(autouse=True)
def clean_cache():
    clear_resolution_cache()
    yield
    clear_resolution_cache()


class TestResolveTermByFields:
    @pytest.mark.asyncio
    async def test_colon_value_travels_uninterpreted(self, httpx_mock):
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "found", "entry_id": CANONICAL}]},
        )
        result = await resolve_term_by_fields("GO:0000278", "GO_SLIM", "onto2")
        assert result == CANONICAL

        payload = json.loads(httpx_mock.get_requests()[0].content)
        assert payload[0]["composite_key"] == {
            "ns": "onto2",
            "type": "term",
            "terminology": "GO_SLIM",
            "value": "GO:0000278",
        }
        assert "entry_id" not in payload[0]

    @pytest.mark.asyncio
    async def test_miss_raises_entity_not_found(self, httpx_mock):
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "not_found"}]},
        )
        with pytest.raises(EntityNotFoundError):
            await resolve_term_by_fields("GO:9999999", "GO_SLIM", "onto2")

    @pytest.mark.asyncio
    async def test_result_is_cached(self, httpx_mock):
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "found", "entry_id": CANONICAL}]},
        )
        first = await resolve_term_by_fields("GO:0000278", "GO_SLIM", "onto2")
        second = await resolve_term_by_fields("GO:0000278", "GO_SLIM", "onto2")
        assert first == second == CANONICAL
        assert len(httpx_mock.get_requests()) == 1


class TestColonMissGuard:
    @pytest.mark.asyncio
    async def test_bare_colon_value_miss_names_the_lossless_doors(self, httpx_mock):
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "not_found"}]},
        )
        with pytest.raises(HTTPException) as exc:
            await resolve_or_404("GO:0000278", "term", "onto2", param_name="term_id")
        assert exc.value.status_code == 404
        assert "onto2:<terminology>:GO:0000278" in exc.value.detail
        assert "terminology=" in exc.value.detail

    @pytest.mark.asyncio
    async def test_qualified_form_miss_gets_no_hint(self, httpx_mock):
        """The 3-part form is lossless — its miss is a genuine not-found."""
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "not_found"}]},
        )
        with pytest.raises(HTTPException) as exc:
            await resolve_or_404(
                "onto2:GO_SLIM:GO:9999999", "term", "onto2", param_name="term_id"
            )
        assert exc.value.status_code == 404
        assert "shorthand cannot parse" not in exc.value.detail

    @pytest.mark.asyncio
    async def test_colon_free_miss_gets_no_hint(self, httpx_mock):
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "not_found"}]},
        )
        with pytest.raises(HTTPException) as exc:
            await resolve_or_404("NOPE", "term", "wip", param_name="term_id")
        assert exc.value.status_code == 404
        assert "shorthand" not in exc.value.detail
