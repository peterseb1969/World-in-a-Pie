"""Tests for synonym resolution — no format-based bypass.

Every ID goes through Registry. UUIDs are sent as ``entry_id`` for
verification, synonyms are sent as ``composite_key`` for resolution.
The ``_looks_like_uuid`` helper only determines which field to use in
the /resolve payload — it never skips the Registry call.
"""

import pytest

from wip_auth.resolve import (
    EntityNotFoundError,
    _build_composite_key,
    _looks_like_uuid,
    clear_resolution_cache,
    resolve_entity_id,
    resolve_entity_ids,
)


@pytest.fixture(autouse=True)
def clean_cache():
    """Clear resolution cache before and after each test."""
    clear_resolution_cache()
    yield
    clear_resolution_cache()


# ===========================================================================
# _looks_like_uuid — routing helper (NOT a bypass)
# ===========================================================================


class TestLooksLikeUuid:
    """Test UUID detection for routing to entry_id vs composite_key."""

    def test_standard_uuid4(self):
        assert _looks_like_uuid("550e8400-e29b-41d4-a716-446655440000") is True

    def test_uppercase_uuid(self):
        assert _looks_like_uuid("550E8400-E29B-41D4-A716-446655440000") is True

    def test_mixed_case_uuid(self):
        assert _looks_like_uuid("550e8400-E29B-41d4-a716-446655440000") is True

    def test_uuid7_style(self):
        assert _looks_like_uuid("01903f5a-7b3c-7d4e-8f9a-0b1c2d3e4f5a") is True

    def test_prefixed_id_varied_prefix_1(self):
        assert _looks_like_uuid("LOV-000001") is False

    def test_prefixed_id_varied_prefix_2(self):
        assert _looks_like_uuid("PROD-000001") is False

    def test_human_readable_value(self):
        assert _looks_like_uuid("GENDER") is False

    def test_empty_string(self):
        assert _looks_like_uuid("") is False

    def test_partial_uuid(self):
        assert _looks_like_uuid("550e8400-e29b-41d4-a716") is False


# ===========================================================================
# _build_composite_key — synonym key construction
# ===========================================================================


class TestBuildCompositeKey:
    """Test composite key construction for resolution."""

    def test_bare_terminology_value(self):
        key = _build_composite_key("GENDER", "terminology", "wip")
        assert key == {"ns": "wip", "type": "terminology", "value": "GENDER"}

    def test_cross_namespace_terminology(self):
        key = _build_composite_key("other:GENDER", "terminology", "wip")
        assert key == {"ns": "other", "type": "terminology", "value": "GENDER"}

    def test_bare_template_value(self):
        key = _build_composite_key("PERSON", "template", "myapp")
        assert key == {"ns": "myapp", "type": "template", "value": "PERSON"}

    def test_term_two_parts(self):
        key = _build_composite_key("GENDER:M", "term", "wip")
        assert key == {"ns": "wip", "type": "term", "terminology": "GENDER", "value": "M"}

    def test_term_three_parts(self):
        key = _build_composite_key("other:GENDER:F", "term", "wip")
        assert key == {"ns": "other", "type": "term", "terminology": "GENDER", "value": "F"}

    def test_term_bare_value(self):
        key = _build_composite_key("approved", "term", "wip")
        assert key == {"ns": "wip", "type": "term", "value": "approved"}


# ===========================================================================
# resolve_entity_id — every ID goes through Registry
# ===========================================================================


class TestResolveEntityId:
    """Test resolve_entity_id with mocked Registry responses."""

    @pytest.mark.asyncio
    async def test_uuid_goes_through_registry(self, httpx_mock):
        """UUID is sent as entry_id to Registry for verification."""
        httpx_mock.add_response(
            url="http://localhost:8001/api/registry/entries/resolve",
            json={
                "results": [
                    {"status": "found", "entry_id": "550e8400-e29b-41d4-a716-446655440000"}
                ]
            },
        )
        result = await resolve_entity_id(
            "550e8400-e29b-41d4-a716-446655440000",
            "terminology",
            "wip",
        )
        assert result == "550e8400-e29b-41d4-a716-446655440000"

        # Verify the request sent entry_id (not composite_key)
        request = httpx_mock.get_requests()[0]
        import json
        payload = json.loads(request.content)
        assert payload[0]["entry_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert "composite_key" not in payload[0]

    @pytest.mark.asyncio
    async def test_fake_uuid_rejected(self, httpx_mock):
        """A UUID-shaped string not in Registry raises EntityNotFoundError."""
        httpx_mock.add_response(
            url="http://localhost:8001/api/registry/entries/resolve",
            json={
                "results": [
                    {"status": "not_found", "entry_id": "00000000-0000-0000-0000-000000000000"}
                ]
            },
        )
        with pytest.raises(EntityNotFoundError):
            await resolve_entity_id(
                "00000000-0000-0000-0000-000000000000",
                "template",
                "wip",
            )

    @pytest.mark.asyncio
    async def test_synonym_resolves_via_composite_key(self, httpx_mock):
        """Human-readable synonym sent as composite_key."""
        httpx_mock.add_response(
            url="http://localhost:8001/api/registry/entries/resolve",
            json={
                "results": [
                    {"status": "found", "entry_id": "550e8400-e29b-41d4-a716-446655440000"}
                ]
            },
        )
        result = await resolve_entity_id("GENDER", "terminology", "wip")
        assert result == "550e8400-e29b-41d4-a716-446655440000"

        # Verify the request sent composite_key (not entry_id)
        import json
        request = httpx_mock.get_requests()[0]
        payload = json.loads(request.content)
        assert payload[0]["composite_key"] == {"ns": "wip", "type": "terminology", "value": "GENDER"}
        assert "entry_id" not in payload[0]

    @pytest.mark.asyncio
    async def test_non_uuid_canonical_id_resolves(self, httpx_mock):
        """Non-UUID canonical IDs (e.g., LOV-000001) go through Registry."""
        httpx_mock.add_response(
            url="http://localhost:8001/api/registry/entries/resolve",
            json={
                "results": [
                    {"status": "found", "entry_id": "LOV-000001"}
                ]
            },
        )
        result = await resolve_entity_id("LOV-000001", "template", "wip")
        assert result == "LOV-000001"

    @pytest.mark.asyncio
    async def test_unknown_synonym_raises(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:8001/api/registry/entries/resolve",
            json={
                "results": [{"status": "not_found"}]
            },
        )
        with pytest.raises(EntityNotFoundError):
            await resolve_entity_id("NONEXISTENT", "terminology", "wip")

    @pytest.mark.asyncio
    async def test_registry_unreachable_raises(self):
        with pytest.raises(EntityNotFoundError):
            await resolve_entity_id("GENDER", "terminology", "wip")

    @pytest.mark.asyncio
    async def test_resolution_is_cached(self, httpx_mock):
        """Second call uses cache — only one HTTP request."""
        httpx_mock.add_response(
            url="http://localhost:8001/api/registry/entries/resolve",
            json={
                "results": [
                    {"status": "found", "entry_id": "550e8400-e29b-41d4-a716-446655440000"}
                ]
            },
        )
        result1 = await resolve_entity_id("GENDER", "terminology", "wip")
        result2 = await resolve_entity_id("GENDER", "terminology", "wip")
        assert result1 == result2
        assert len(httpx_mock.get_requests()) == 1

    @pytest.mark.asyncio
    async def test_uuid_cached_after_verification(self, httpx_mock):
        """UUID verification is also cached."""
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        httpx_mock.add_response(
            url="http://localhost:8001/api/registry/entries/resolve",
            json={"results": [{"status": "found", "entry_id": uuid}]},
        )
        await resolve_entity_id(uuid, "template", "wip")
        await resolve_entity_id(uuid, "template", "wip")
        assert len(httpx_mock.get_requests()) == 1


# ===========================================================================
# resolve_entity_ids — batch resolution
# ===========================================================================


class TestResolveEntityIds:
    """Test batch resolution."""

    @pytest.mark.asyncio
    async def test_all_uuids_verified(self, httpx_mock):
        """UUIDs are verified against Registry, not passed through."""
        ids = [
            "550e8400-e29b-41d4-a716-446655440000",
            "660e8400-e29b-41d4-a716-446655440001",
        ]
        httpx_mock.add_response(
            url="http://localhost:8001/api/registry/entries/resolve",
            json={
                "results": [
                    {"status": "found", "entry_id": ids[0]},
                    {"status": "found", "entry_id": ids[1]},
                ]
            },
        )
        result = await resolve_entity_ids(ids, "template", "wip")
        assert result == {id_: id_ for id_ in ids}

    @pytest.mark.asyncio
    async def test_mixed_uuids_and_synonyms(self, httpx_mock):
        """Mix of UUIDs and synonyms — all go through Registry."""
        uuid_id = "550e8400-e29b-41d4-a716-446655440000"
        httpx_mock.add_response(
            url="http://localhost:8001/api/registry/entries/resolve",
            json={
                "results": [
                    {"status": "found", "entry_id": uuid_id},
                    {"status": "found", "entry_id": "660e8400-e29b-41d4-a716-446655440001"},
                ]
            },
        )
        result = await resolve_entity_ids(
            [uuid_id, "GENDER"], "terminology", "wip"
        )
        assert result[uuid_id] == uuid_id
        assert result["GENDER"] == "660e8400-e29b-41d4-a716-446655440001"

    @pytest.mark.asyncio
    async def test_fake_uuid_in_batch_rejected(self, httpx_mock):
        """A fake UUID in a batch causes EntityNotFoundError."""
        httpx_mock.add_response(
            url="http://localhost:8001/api/registry/entries/resolve",
            json={
                "results": [
                    {"status": "not_found", "entry_id": "00000000-0000-0000-0000-000000000000"},
                ]
            },
        )
        with pytest.raises(EntityNotFoundError):
            await resolve_entity_ids(
                ["00000000-0000-0000-0000-000000000000"], "template", "wip"
            )
