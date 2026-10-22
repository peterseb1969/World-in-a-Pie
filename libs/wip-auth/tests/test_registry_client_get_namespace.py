"""get_namespace — one namespace's Registry record, or None when absent (CASE-916).

reporting-sync decides from this answer whether a deleted namespace has been
re-created. Only a 404 may mean "no such namespace": any other failure,
including an unreachable Registry, must raise, so a caller never concludes a
namespace is gone from a lookup that did not happen.
"""

import httpx
import pytest

from wip_auth.registry_client import RegistryClientBase, RegistryError

BASE = "http://registry.test:8001"
URL = f"{BASE}/api/registry/namespaces/books"


@pytest.mark.asyncio
async def test_found_returns_the_record(httpx_mock):
    httpx_mock.add_response(url=URL, json={"prefix": "books", "status": "active"})
    client = RegistryClientBase(base_url=BASE, api_key="k")
    assert await client.get_namespace("books") == {"prefix": "books", "status": "active"}
    assert httpx_mock.get_requests()[0].headers["X-API-Key"] == "k"


@pytest.mark.asyncio
async def test_404_returns_none(httpx_mock):
    httpx_mock.add_response(url=URL, status_code=404, json={"detail": "Namespace not found: books"})
    client = RegistryClientBase(base_url=BASE, api_key="k")
    assert await client.get_namespace("books") is None


@pytest.mark.asyncio
async def test_other_status_raises(httpx_mock):
    httpx_mock.add_response(url=URL, status_code=503)
    client = RegistryClientBase(base_url=BASE, api_key="k")
    with pytest.raises(RegistryError):
        await client.get_namespace("books")


@pytest.mark.asyncio
async def test_unreachable_registry_raises(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=URL)
    client = RegistryClientBase(base_url=BASE, api_key="k")
    with pytest.raises(RegistryError):
        await client.get_namespace("books")
