"""Read-door resolution parity with the write door.

The GET doors resolve through /entries/resolve, whose composite keys cannot
match every synonym shape (a document's value synonym is the bare
identity-values dict), and whose entry_id field was only ever sent for
UUID-shaped strings — so a prefixed canonical id 404ed exactly when the
caller supplied its own namespace, and the qualified '<ns>:<id>' string the
platform itself stores in reference fields never dereferenced on read.
These tests pin the three-part fix: dual-field /resolve payloads, the
by-id miss fallback (the write door's transport), and qualified
identifiers carrying their own namespace context through resolve_or_404.
"""

import json

import pytest

from wip_auth.resolve import (
    EntityNotFoundError,
    clear_resolution_cache,
    resolve_entity_id,
)

RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"
BY_ID_URL = "http://localhost:8001/api/registry/entries/lookup/by-id"
CANONICAL = "0195c1a0-0000-7000-8000-000000000001"


@pytest.fixture(autouse=True)
def clean_cache():
    clear_resolution_cache()
    yield
    clear_resolution_cache()


def _found(entry_id=CANONICAL):
    return {"results": [{"status": "found", "entry_id": entry_id}]}


def _not_found():
    return {"results": [{"status": "not_found"}]}


class TestDualFieldPayload:
    @pytest.mark.asyncio
    async def test_prefixed_canonical_sends_entry_id_candidate(self, httpx_mock):
        """A prefixed canonical id reaches the endpoint's entry-id-first
        path even though it is not UUID-shaped."""
        httpx_mock.add_response(url=RESOLVE_URL, json=_found("ns1-D000001"))
        result = await resolve_entity_id("ns1-D000001", "document", "ns1")
        assert result == "ns1-D000001"
        payload = json.loads(httpx_mock.get_requests()[0].content)
        assert payload[0]["entry_id"] == "ns1-D000001"
        assert payload[0]["composite_key"] == {
            "ns": "ns1", "type": "document", "value": "ns1-D000001",
        }

    @pytest.mark.asyncio
    async def test_qualified_identifier_splits_for_entry_id(self, httpx_mock):
        """'<ns>:<id>' — the entry_id candidate is the value half (an
        entry_id never carries a qualifier), the composite key resolves in
        the named namespace."""
        httpx_mock.add_response(url=RESOLVE_URL, json=_found("ns1-D000001"))
        result = await resolve_entity_id("ns1:ns1-D000001", "document", "other-ns")
        assert result == "ns1-D000001"
        payload = json.loads(httpx_mock.get_requests()[0].content)
        assert payload[0]["entry_id"] == "ns1-D000001"
        assert payload[0]["composite_key"] == {
            "ns": "ns1", "type": "document", "value": "ns1-D000001",
        }

    @pytest.mark.asyncio
    async def test_term_identifiers_send_no_entry_id_candidate(self, httpx_mock):
        """Terms keep their strict forms — composite key only."""
        httpx_mock.add_response(url=RESOLVE_URL, json=_found())
        await resolve_entity_id("ns1:GENDER:M", "term", "other-ns")
        payload = json.loads(httpx_mock.get_requests()[0].content)
        assert "entry_id" not in payload[0]
        assert payload[0]["composite_key"] == {
            "ns": "ns1", "type": "term", "terminology": "GENDER", "value": "M",
        }

    @pytest.mark.asyncio
    async def test_uuid_payload_unchanged(self, httpx_mock):
        httpx_mock.add_response(url=RESOLVE_URL, json=_found())
        await resolve_entity_id(CANONICAL, "document", "ns1")
        payload = json.loads(httpx_mock.get_requests()[0].content)
        assert payload[0]["entry_id"] == CANONICAL
        assert "composite_key" not in payload[0]


class TestByIdMissFallback:
    @pytest.mark.asyncio
    async def test_resolve_miss_falls_back_to_by_id(self, httpx_mock):
        """search_values matches (value-form document refs) live only on
        the by-id endpoint — the write door's transport."""
        httpx_mock.add_response(url=RESOLVE_URL, json=_not_found())
        httpx_mock.add_response(url=BY_ID_URL, json=_found())
        result = await resolve_entity_id("SAMP-1", "document", "ns1")
        assert result == CANONICAL
        by_id_payload = json.loads(httpx_mock.get_requests()[1].content)
        assert by_id_payload[0] == {
            "entry_id": "SAMP-1", "namespace": "ns1", "entity_type": "documents",
        }

    @pytest.mark.asyncio
    async def test_qualified_miss_uses_prefix_namespace(self, httpx_mock):
        httpx_mock.add_response(url=RESOLVE_URL, json=_not_found())
        httpx_mock.add_response(url=BY_ID_URL, json=_found())
        result = await resolve_entity_id("ns1:SAMP-1", "document", "other-ns")
        assert result == CANONICAL
        by_id_payload = json.loads(httpx_mock.get_requests()[1].content)
        assert by_id_payload[0]["entry_id"] == "SAMP-1"
        assert by_id_payload[0]["namespace"] == "ns1"

    @pytest.mark.asyncio
    async def test_both_miss_raises_original_error(self, httpx_mock):
        httpx_mock.add_response(url=RESOLVE_URL, json=_not_found())
        httpx_mock.add_response(url=BY_ID_URL, json=_not_found())
        with pytest.raises(EntityNotFoundError) as exc:
            await resolve_entity_id("NOPE", "document", "ns1")
        # The error names the identifier as given, not the fallback's view.
        assert "NOPE" in str(exc.value)

    @pytest.mark.asyncio
    async def test_terms_never_fall_back(self, httpx_mock):
        """A term miss is final: its strict forms resolve via composite
        keys only, and by-id's raw string match would reintroduce the
        ambiguity the 422 guard exists to prevent."""
        httpx_mock.add_response(url=RESOLVE_URL, json=_not_found())
        with pytest.raises(EntityNotFoundError):
            await resolve_entity_id("ns1:GENDER:M", "term", "ns1")
        assert len(httpx_mock.get_requests()) == 1

    @pytest.mark.asyncio
    async def test_uuid_miss_never_falls_back(self, httpx_mock):
        """A UUID that fails entry_id verification is missing, full stop —
        by-id's search_values could only match it as somebody's stored
        value, which is not identity."""
        httpx_mock.add_response(url=RESOLVE_URL, json=_not_found())
        with pytest.raises(EntityNotFoundError):
            await resolve_entity_id(CANONICAL, "document", "ns1")
        assert len(httpx_mock.get_requests()) == 1


class TestResolveOr404QualifiedContext:
    """A qualified identifier carries its own namespace context — a
    multi-namespace key omitting ?namespace= must not degrade it to the
    blind pass-through."""

    @pytest.fixture(autouse=True)
    def no_identity(self, monkeypatch):
        from wip_auth import fastapi_helpers
        monkeypatch.setattr(
            fastapi_helpers, "_derive_namespace_from_identity", lambda: None
        )

    @pytest.mark.asyncio
    async def test_qualified_resolves_without_namespace_param(self, httpx_mock):
        from wip_auth.fastapi_helpers import resolve_or_404
        httpx_mock.add_response(url=RESOLVE_URL, json=_found())
        result = await resolve_or_404("ns1:ns1-D000001", "document", None)
        assert result == CANONICAL

    @pytest.mark.asyncio
    async def test_qualified_miss_passes_through_raw(self, httpx_mock):
        """On a miss the raw id comes back — the outcome the old
        pass-through produced — so downstream by-value branches keep
        working; the fix only ADDS resolution."""
        from wip_auth.fastapi_helpers import resolve_or_404
        httpx_mock.add_response(url=RESOLVE_URL, json=_not_found())
        httpx_mock.add_response(url=BY_ID_URL, json=_not_found())
        result = await resolve_or_404("ns1:whatever", "document", None)
        assert result == "ns1:whatever"

    @pytest.mark.asyncio
    async def test_qualified_miss_with_strict_raises_404(self, httpx_mock):
        """strict callers have no downstream fallback: a miss with real
        (identifier-carried) context is a loud 404, exactly like the
        explicit-namespace path — never a silent match-nothing filter."""
        from fastapi import HTTPException

        from wip_auth.fastapi_helpers import resolve_or_404
        httpx_mock.add_response(url=RESOLVE_URL, json=_not_found())
        httpx_mock.add_response(url=BY_ID_URL, json=_not_found())
        with pytest.raises(HTTPException) as exc:
            await resolve_or_404(
                "ns1:whatever", "document", None, strict=True
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_bare_no_context_pass_through_unchanged(self, httpx_mock):
        """No colon, no namespace, non-strict: the historical warning
        pass-through stays byte-identical — no request leaves the process."""
        from wip_auth.fastapi_helpers import resolve_or_404
        result = await resolve_or_404("SAMP-1", "document", None)
        assert result == "SAMP-1"
        assert len(httpx_mock.get_requests()) == 0
