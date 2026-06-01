"""CASE-435 — the string-value document-reference path no longer falls back to
a service-local Mongo business-key lookup.

Verified by probe that the Registry already resolves a registered doc's
identity-value string (its synonym flattens into search_values), so the string
fallback was unreachable for correctly-registered docs and only resolved values
the Registry doesn't know (a bypass). It is removed; the Registry is the only
resolver for string refs (a miss is logged). The dict / composite business-key
path keeps `_lookup_by_business_key` — it has no Registry equivalent.
"""

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from document_store.services.document_service import get_document_service


class _Result:
    def __init__(self):
        self.errors = []

    def add_error(self, **kwargs):
        self.errors.append(kwargs)


@pytest.mark.asyncio
class TestStringRefNoMongoFallback:
    async def test_string_miss_does_not_fall_back_to_mongo(
        self, client: AsyncClient, auth_headers: dict, monkeypatch
    ):
        vs = get_document_service().validation_service
        # Registry misses; the business-key lookup is a spy that must NOT be called.
        monkeypatch.setattr(vs, "_resolve_via_registry", AsyncMock(return_value=None))
        spy = AsyncMock(return_value="SHOULD_NOT_BE_CALLED")
        monkeypatch.setattr(vs, "_lookup_by_business_key", spy)

        result = _Result()
        doc = await vs._resolve_document_reference(
            value="unregistered-string-ref",
            target_templates=["PERSON"],
            result=result,
            field_path="ref_field",
            namespace="wip",
        )

        spy.assert_not_called()  # string branch no longer falls back to Mongo
        assert doc is None
        assert any(e.get("code") == "reference_not_found" for e in result.errors)

    async def test_dict_ref_still_uses_business_key(
        self, client: AsyncClient, auth_headers: dict, monkeypatch
    ):
        vs = get_document_service().validation_service
        spy = AsyncMock(return_value=None)
        monkeypatch.setattr(vs, "_lookup_by_business_key", spy)

        result = _Result()
        await vs._resolve_document_reference(
            value={"national_id": "123456789"},
            target_templates=["PERSON"],
            result=result,
            field_path="ref_field",
            namespace="wip",
        )

        spy.assert_awaited_once()  # composite-key path preserved
