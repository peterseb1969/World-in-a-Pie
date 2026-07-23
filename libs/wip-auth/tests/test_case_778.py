"""Strict term addressing at the resolver helpers.

A term's identity is the tuple (namespace, terminology, value). The 2-part
'TERMINOLOGY:VALUE' shorthand is a lossy serialization of it — no parser
can tell a structural colon from a colon inside the value (OBO ids like
GO:0000278), so a 2-part string can silently resolve through a decoy
terminology. On a write door that mutates the wrong entity; on a read door
it feeds a silently wrong answer. Term doors therefore reject the 2-part
form outright (422, request-shape error) and accept only the unambiguous
forms: canonical UUID, fully qualified 'ns:terminology:value', or the
field form (terminology scope + opaque value).

Contract under test:

  - resolve_or_404 and resolve_bulk_ids reject 2-part term identifiers
    with 422 naming the three accepted forms, before any Registry call,
    regardless of namespace availability. Non-term entity types keep
    their 2-part 'NS:VALUE' form untouched.
  - resolve_bulk_ids' terminology / terminology_field scopes switch
    non-UUID identifiers to the structured field door: the value travels
    opaque, colons included, and no rejection applies.
  - bypass_cache=True makes bulk resolution ask Registry even when a
    cached answer exists — write doors must not pin stale IDs into
    durable state (deprecation pointers, relation endpoints).
"""

import json

import pytest
from fastapi import HTTPException

from wip_auth.fastapi_helpers import resolve_bulk_ids, resolve_or_404
from wip_auth.resolve import clear_resolution_cache

CANONICAL = "550e8400-e29b-41d4-a716-446655440000"
CANONICAL_2 = "660e8400-e29b-41d4-a716-446655440001"
RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"


@pytest.fixture(autouse=True)
def clean_cache():
    clear_resolution_cache()
    yield
    clear_resolution_cache()


class TestRejectTwoPartForm:
    @pytest.mark.asyncio
    async def test_single_item_two_part_rejected_before_registry(self, httpx_mock):
        with pytest.raises(HTTPException) as exc:
            await resolve_or_404("GO:0000278", "term", "onto2", param_name="term_id")
        assert exc.value.status_code == 422
        assert "GO:0000278" in exc.value.detail
        assert "ns:terminology:value" in exc.value.detail
        assert "terminology=" in exc.value.detail
        assert "UUID" in exc.value.detail
        assert len(httpx_mock.get_requests()) == 0

    @pytest.mark.asyncio
    async def test_rejected_even_without_namespace_context(self, httpx_mock):
        """Ambiguity is a property of the form — the no-namespace
        pass-through path must not smuggle a 2-part string downstream."""
        with pytest.raises(HTTPException) as exc:
            await resolve_or_404("GENDER:M", "term", None, param_name="term_id")
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_bulk_two_part_rejected_whole_request(self, httpx_mock):
        items = [{"term_id": CANONICAL}, {"term_id": "GENDER:M"}]
        with pytest.raises(HTTPException) as exc:
            await resolve_bulk_ids(items, "term_id", "term", "wip")
        assert exc.value.status_code == 422
        assert "GENDER:M" in exc.value.detail
        assert len(httpx_mock.get_requests()) == 0

    @pytest.mark.asyncio
    async def test_three_part_and_bare_and_uuid_pass(self, httpx_mock):
        for _ in range(3):
            httpx_mock.add_response(
                url=RESOLVE_URL,
                json={"results": [{"status": "found", "entry_id": CANONICAL}]},
            )
        for raw in ("wip:GENDER:M", "APPROVED", CANONICAL):
            assert await resolve_or_404(raw, "term", "wip") == CANONICAL

    @pytest.mark.asyncio
    async def test_non_term_two_part_form_is_untouched(self, httpx_mock):
        """'NS:VALUE' is the documented cross-namespace form for
        terminologies, templates, and documents — the term guard must not
        reach it."""
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "found", "entry_id": CANONICAL}]},
        )
        result = await resolve_or_404("wip:GENDER", "terminology", "onto2")
        assert result == CANONICAL


class TestBulkFieldDoor:
    @pytest.mark.asyncio
    async def test_call_scope_resolves_opaque_value_by_fields(self, httpx_mock):
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "found", "entry_id": CANONICAL}]},
        )
        items = [{"term_id": "GO:0000278"}]
        await resolve_bulk_ids(
            items, "term_id", "term", "onto2", terminology="GO_SLIM",
        )
        assert items[0]["term_id"] == CANONICAL

        payload = json.loads(httpx_mock.get_requests()[0].content)
        assert payload[0]["composite_key"] == {
            "ns": "onto2",
            "type": "term",
            "terminology": "GO_SLIM",
            "value": "GO:0000278",
        }

    @pytest.mark.asyncio
    async def test_call_scope_uuid_items_verify_as_entry_id(self, httpx_mock):
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "found", "entry_id": CANONICAL}]},
        )
        items = [{"term_id": CANONICAL}]
        await resolve_bulk_ids(
            items, "term_id", "term", "onto2", terminology="GO_SLIM",
        )
        payload = json.loads(httpx_mock.get_requests()[0].content)
        assert payload[0] == {"entry_id": CANONICAL}

    @pytest.mark.asyncio
    async def test_per_item_scope_via_terminology_field(self, httpx_mock):
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "found", "entry_id": CANONICAL}]},
        )
        items = [{"source_term_id": "GO:0000278", "source_terminology": "GO_SLIM"}]
        await resolve_bulk_ids(
            items, "source_term_id", "term", "onto2",
            terminology_field="source_terminology",
        )
        assert items[0]["source_term_id"] == CANONICAL
        payload = json.loads(httpx_mock.get_requests()[0].content)
        assert payload[0]["composite_key"]["value"] == "GO:0000278"
        assert payload[0]["composite_key"]["terminology"] == "GO_SLIM"

    @pytest.mark.asyncio
    async def test_item_without_scope_still_guarded(self, httpx_mock):
        items = [{"source_term_id": "GO:0000278"}]
        with pytest.raises(HTTPException) as exc:
            await resolve_bulk_ids(
                items, "source_term_id", "term", "onto2",
                terminology_field="source_terminology",
            )
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_field_door_miss_passes_through_per_item(self, httpx_mock):
        """Resolution misses stay per-item (bulk convention): the raw value
        flows on and the downstream service reports the item error."""
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "not_found"}]},
        )
        items = [{"term_id": "GO:9999999"}]
        await resolve_bulk_ids(
            items, "term_id", "term", "onto2", terminology="GO_SLIM",
        )
        assert items[0]["term_id"] == "GO:9999999"


class TestBulkBypassCache:
    @pytest.mark.asyncio
    async def test_write_path_resolution_skips_cache_read(self, httpx_mock):
        """A cached answer must not serve a write: after the cache holds
        CANONICAL, a re-pointed Registry answer (CANONICAL_2) must win on
        the next bypass_cache resolution."""
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "found", "entry_id": CANONICAL}]},
        )
        items = [{"term_id": "APPROVED"}]
        await resolve_bulk_ids(items, "term_id", "term", "wip")
        assert items[0]["term_id"] == CANONICAL

        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "found", "entry_id": CANONICAL_2}]},
        )
        items = [{"term_id": "APPROVED"}]
        await resolve_bulk_ids(items, "term_id", "term", "wip", bypass_cache=True)
        assert items[0]["term_id"] == CANONICAL_2
        assert len(httpx_mock.get_requests()) == 2

    @pytest.mark.asyncio
    async def test_default_read_path_still_caches(self, httpx_mock):
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "found", "entry_id": CANONICAL}]},
        )
        for _ in range(2):
            items = [{"term_id": "APPROVED"}]
            await resolve_bulk_ids(items, "term_id", "term", "wip")
            assert items[0]["term_id"] == CANONICAL
        assert len(httpx_mock.get_requests()) == 1

    @pytest.mark.asyncio
    async def test_field_door_bypass_cache_reaches_registry(self, httpx_mock):
        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "found", "entry_id": CANONICAL}]},
        )
        items = [{"term_id": "GO:0000278"}]
        await resolve_bulk_ids(
            items, "term_id", "term", "onto2", terminology="GO_SLIM",
        )

        httpx_mock.add_response(
            url=RESOLVE_URL,
            json={"results": [{"status": "found", "entry_id": CANONICAL_2}]},
        )
        items = [{"term_id": "GO:0000278"}]
        await resolve_bulk_ids(
            items, "term_id", "term", "onto2", terminology="GO_SLIM",
            bypass_cache=True,
        )
        assert items[0]["term_id"] == CANONICAL_2
        assert len(httpx_mock.get_requests()) == 2
