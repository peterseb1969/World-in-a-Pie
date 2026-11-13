"""A Registry item that reports an error is an outage, never a miss; a
refusal of what the caller sent is an answer; a rollback's hard-delete that
cannot reach the Registry is a RegistryError its callers handle.

Provenance: CASE-1264 (commit-review backfill, findings on ae22e496 and
b6b06d64). The resolve layer read a 200 carrying ``status: "error"`` as not
found (a 404, a missing reference); the client's own lookups already raised
RegistryUnavailableError for it. That guard also read a caller refusal
(``invalid_entity_type``) as an outage, answered "retry shortly".
hard_delete_entry let an httpx error escape every caller's
``except RegistryError`` (document-store and def-store rollbacks) as a 500.
"""

import httpx
import pytest

from wip_auth.registry_client import (
    RegistryClientBase,
    RegistryError,
    RegistryUnavailableError,
    _raise_item_error,
)
from wip_auth.resolve import clear_resolution_cache, resolve_entity_id, resolve_entity_ids

RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"
ERROR_ITEM = {"results": [{"status": "error", "error": "database fault", "error_code": "internal_error"}]}


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    # Set here, not left to an earlier test: the resolver refuses to run
    # without the key it calls the Registry with.
    monkeypatch.setenv("REGISTRY_API_KEY", "k")
    monkeypatch.setenv("REGISTRY_URL", "http://localhost:8001")
    clear_resolution_cache()
    yield
    clear_resolution_cache()


@pytest.mark.asyncio
async def test_a_resolve_error_item_is_an_outage(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json=ERROR_ITEM)
    with pytest.raises(RegistryUnavailableError, match="database fault"):
        await resolve_entity_id("wip:ALPHA", "template", "wip", bypass_cache=True)


@pytest.mark.asyncio
async def test_a_batch_resolve_error_item_is_an_outage(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json=ERROR_ITEM)
    with pytest.raises(RegistryUnavailableError):
        await resolve_entity_ids(["wip:ALPHA"], "template", "wip", bypass_cache=True, strict=False)


def test_a_caller_refusal_is_an_answer_not_an_outage():
    refused = [{"status": "error", "error": "unknown entity type", "error_code": "invalid_entity_type"}]
    with pytest.raises(RegistryError) as info:
        _raise_item_error(refused, "candidate lookup", "x")
    assert not isinstance(info.value, RegistryUnavailableError)
    with pytest.raises(RegistryUnavailableError):
        _raise_item_error(ERROR_ITEM["results"], "candidate lookup", "x")
    _raise_item_error([{"status": "not_found"}], "candidate lookup", "x")  # an answer: passes


@pytest.mark.asyncio
async def test_a_hard_delete_that_cannot_reach_the_registry_is_a_registry_error(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    client = RegistryClientBase(base_url="http://registry.test", api_key="k")
    with pytest.raises(RegistryError, match="unreachable"):
        await client.hard_delete_entry("e1", rollback_uncommitted=True)

