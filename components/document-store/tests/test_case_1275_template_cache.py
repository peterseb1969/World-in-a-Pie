"""A pinned template lookup that finds nothing leaves the cache as it was.

Provenance: CASE-1275 (104b7359 R8). The pinned-version path stored whatever the
read returned, including None, while its comment claimed a miss is not kept. A null
entry can never serve a hit — the read treats it as a miss — carries no namespace
for the stamp check to evict it by, so it outlives every eviction, and it counts
in the reported cache size. Hermetic: the store read is stubbed, no HTTP, no DB.

"""

from unittest.mock import AsyncMock

import pytest

from document_store.services.template_store_client import TemplateStoreClient


def _client() -> TemplateStoreClient:
    return TemplateStoreClient(base_url="http://unused", api_key="k")


def _tpl(tid: str, version: int) -> dict:
    return {"template_id": tid, "version": version, "namespace": "ns1", "status": "active"}


@pytest.mark.asyncio
async def test_a_pinned_miss_stores_nothing(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "get_template", AsyncMock(return_value=None))

    assert await c.get_template_resolved("A", version=1) is None
    assert c._template_cache == {}
    assert c.get_cache_stats()["template_cache_size"] == 0


@pytest.mark.asyncio
async def test_a_version_that_appears_after_a_miss_is_found_at_once(monkeypatch):
    """The miss did not take a slot: the next read asks the store and keeps what
    it gets."""
    c = _client()
    missing = AsyncMock(return_value=None)
    monkeypatch.setattr(c, "get_template", missing)
    assert await c.get_template_resolved("A", version=1) is None

    found = _tpl("A", 1)
    monkeypatch.setattr(c, "get_template", AsyncMock(return_value=found))
    assert await c.get_template_resolved("A", version=1) is found
    assert c._template_cache == {"A:v1": found}

    third = AsyncMock(return_value=found)
    monkeypatch.setattr(c, "get_template", third)
    assert await c.get_template_resolved("A", version=1) is found
    assert third.await_count == 0
