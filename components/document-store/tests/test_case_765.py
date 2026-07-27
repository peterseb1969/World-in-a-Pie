"""Regression coverage for CASE-765 — namespace-scoped terminology resolution
in document-store's term validation client.

Before this fix, DefStoreClient fell back to an UNSCOPED by-value fetch when
a terminology ref wasn't a resolvable ID, and cross-cached every fetched
terminology under its bare value — so with the same terminology value live
in several namespaces, documents could validate against another namespace's
vocabulary (fetch half), and cache hits could cross namespaces even with a
correct fetch (cache half).

Contract under test:

  - A canonical UUID ref short-circuits: no Registry round-trip, fetch by
    that UUID.
  - A value-form ref resolves through Registry with the validating
    document's namespace as context; the fetch then uses the canonical
    UUID. The by-value fallback no longer exists.
  - A value-form ref with no namespace context, or one Registry cannot
    resolve, reports terminology-not-found — never an arbitrary pick.
  - The terminology cache keys on canonical UUIDs only: the same value
    resolving differently in two namespaces yields two distinct cache
    entries, never a cross-namespace hit.
"""

from unittest.mock import AsyncMock, patch

import pytest

from document_store.services.def_store_client import DefStoreClient

UUID_A = "019f0000-0000-7000-8000-00000000000a"
UUID_B = "019f0000-0000-7000-8000-00000000000b"


def _terminology(uuid: str, ns: str) -> dict:
    return {
        "terminology_id": uuid,
        "namespace": ns,
        "value": "STATUS",
        "case_sensitive": False,
        "terms": [{"term_id": f"T-{ns}", "value": f"{ns}-only", "aliases": []}],
        "_lookup": {f"{ns}-only": {"term": {"term_id": f"T-{ns}", "value": f"{ns}-only"}, "matched_via": "value"}},
    }


def _client_with_fetch(fetch_mock) -> DefStoreClient:
    client = DefStoreClient(base_url="http://def-store-not-called")
    client._fetch_terminology_with_terms = fetch_mock
    return client


@pytest.mark.asyncio
async def test_uuid_ref_short_circuits_registry():
    fetch = AsyncMock(return_value=_terminology(UUID_A, "ns-a"))
    client = _client_with_fetch(fetch)

    with patch(
        "document_store.services.def_store_client.resolve_entity_id",
        new_callable=AsyncMock,
    ) as resolve:
        result = await client.validate_value(UUID_A, "ns-a-only", namespace="ns-a")

    assert result["valid"] is True
    resolve.assert_not_awaited()
    fetch.assert_awaited_once_with(UUID_A)


@pytest.mark.asyncio
async def test_value_ref_resolves_in_document_namespace():
    fetch = AsyncMock(return_value=_terminology(UUID_A, "ns-a"))
    client = _client_with_fetch(fetch)

    with patch(
        "document_store.services.def_store_client.resolve_entity_id",
        new_callable=AsyncMock,
        return_value=UUID_A,
    ) as resolve:
        result = await client.validate_value("STATUS", "ns-a-only", namespace="ns-a")

    assert result["valid"] is True
    resolve.assert_awaited_once_with("STATUS", "terminology", "ns-a")
    fetch.assert_awaited_once_with(UUID_A)


@pytest.mark.asyncio
async def test_value_ref_without_namespace_is_not_found():
    fetch = AsyncMock()
    client = _client_with_fetch(fetch)

    with patch(
        "document_store.services.def_store_client.resolve_entity_id",
        new_callable=AsyncMock,
    ) as resolve:
        result = await client.validate_value("STATUS", "anything")

    assert result["valid"] is False
    assert "not found" in result["error"]
    resolve.assert_not_awaited()
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_unresolvable_value_ref_is_not_found():
    from wip_auth.resolve import EntityNotFoundError

    fetch = AsyncMock()
    client = _client_with_fetch(fetch)

    with patch(
        "document_store.services.def_store_client.resolve_entity_id",
        new_callable=AsyncMock,
        side_effect=EntityNotFoundError("STATUS", "terminology"),
    ):
        result = await client.validate_value("STATUS", "anything", namespace="ns-a")

    assert result["valid"] is False
    assert "not found" in result["error"]
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_never_crosses_namespaces():
    """The same value ref in two namespaces yields two cache entries.

    Validation in ns-a warms the cache; validation in ns-b with the same
    bare value ref must fetch ns-b's terminology, not hit ns-a's entry.
    """
    by_uuid = {UUID_A: _terminology(UUID_A, "ns-a"), UUID_B: _terminology(UUID_B, "ns-b")}
    fetch = AsyncMock(side_effect=lambda uuid: by_uuid[uuid])
    client = _client_with_fetch(fetch)

    async def resolve_by_ns(ref, entity_type, namespace):
        return UUID_A if namespace == "ns-a" else UUID_B

    with patch(
        "document_store.services.def_store_client.resolve_entity_id",
        new_callable=AsyncMock,
        side_effect=resolve_by_ns,
    ):
        first = await client.validate_value("STATUS", "ns-a-only", namespace="ns-a")
        second = await client.validate_value("STATUS", "ns-b-only", namespace="ns-b")
        # ns-b's vocabulary must not accept ns-a's term
        cross = await client.validate_value("STATUS", "ns-a-only", namespace="ns-b")

    assert first["valid"] is True
    assert second["valid"] is True
    assert cross["valid"] is False
    fetched = {call.args[0] for call in fetch.await_args_list}
    assert fetched == {UUID_A, UUID_B}


@pytest.mark.asyncio
async def test_bulk_resolves_each_ref_in_namespace_context():
    fetch = AsyncMock(return_value=_terminology(UUID_A, "ns-a"))
    client = _client_with_fetch(fetch)

    with patch(
        "document_store.services.def_store_client.resolve_entity_id",
        new_callable=AsyncMock,
        return_value=UUID_A,
    ) as resolve:
        results = await client.validate_values_bulk(
            [{"terminology_ref": "STATUS", "value": "ns-a-only"}],
            namespace="ns-a",
        )

    assert results[0]["valid"] is True
    assert all(call.args == ("STATUS", "terminology", "ns-a") for call in resolve.await_args_list)
