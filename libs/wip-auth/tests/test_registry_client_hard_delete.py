"""hard_delete_entry — the per-item result decides, not the HTTP status.

The Registry's delete endpoint is bulk-first: a refused hard-delete (the
namespace's deletion_mode gate) comes back as a per-item error inside HTTP
200. Reading only the status code turned every refusal into a silent
success and left entries whose synonyms resolve to deleted objects.
"""

import json

import pytest

from wip_auth.registry_client import RegistryClientBase, RegistryError

BASE = "http://registry.test:8001"
URL = f"{BASE}/api/registry/entries"


def _reply(httpx_mock, status, error=None):
    item = {"index": 0, "status": status, "registry_id": "e-1"}
    if error:
        item["error"] = error
    httpx_mock.add_response(url=URL, method="DELETE", json={"results": [item]})


@pytest.mark.asyncio
async def test_deleted_returns_true(httpx_mock):
    _reply(httpx_mock, "deleted")
    assert await RegistryClientBase(base_url=BASE, api_key="k").hard_delete_entry("e-1") is True


@pytest.mark.asyncio
async def test_already_gone_returns_false(httpx_mock):
    _reply(httpx_mock, "not_found")
    assert await RegistryClientBase(base_url=BASE, api_key="k").hard_delete_entry("e-1") is False


@pytest.mark.asyncio
async def test_refusal_inside_200_raises(httpx_mock):
    _reply(httpx_mock, "error", "Hard-delete requires namespace deletion_mode='full' (currently 'retain')")
    with pytest.raises(RegistryError, match="deletion_mode"):
        await RegistryClientBase(base_url=BASE, api_key="k").hard_delete_entry("e-1")


@pytest.mark.asyncio
async def test_owner_hard_deleted_flag_is_sent(httpx_mock):
    _reply(httpx_mock, "deleted")
    await RegistryClientBase(base_url=BASE, api_key="k").hard_delete_entry("e-1", owner_hard_deleted=True)
    (item,) = json.loads(httpx_mock.get_requests()[0].content)
    assert item["owner_hard_deleted"] is True
    assert item["hard_delete"] is True


@pytest.mark.asyncio
async def test_default_request_omits_the_new_flag(httpx_mock):
    """A Registry predating owner_hard_deleted rejects unknown fields (strict
    request model). During a rollout services restart at different moments,
    so a request that does not use the flag must not mention it."""
    _reply(httpx_mock, "deleted")
    await RegistryClientBase(base_url=BASE, api_key="k").hard_delete_entry("e-1")
    (item,) = json.loads(httpx_mock.get_requests()[0].content)
    assert "owner_hard_deleted" not in item
