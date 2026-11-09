"""No terminology deletion goes ahead on an answer Template Store did not give,
and an unhealthy def-store does not answer 200.

force=true means "deactivate even though templates use it", not "even
though nobody could check": a soft delete asks Template Store whatever force
says, and an unverifiable count refuses it, force included. Only a KNOWN
count is overridden by force. /health answers 503 with its body when the
database is unreachable, so a probe reading only the status code sees it.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

API = "/api/def-store"


async def _terminology(client, auth_headers, value):
    r = await client.post(f"{API}/terminologies", headers=auth_headers,
                          json=[{"value": value, "label": value, "namespace": "wip"}])
    assert r.json()["succeeded"] == 1, r.text
    return r.json()["results"][0]["id"]


@pytest.mark.asyncio
async def test_forced_soft_delete_refused_when_template_store_cannot_answer(
    client: AsyncClient, auth_headers: dict, template_store
):
    tid = await _terminology(client, auth_headers, "probe_1186_force")
    template_store.unreachable = True

    r = await client.request("DELETE", f"{API}/terminologies", headers=auth_headers,
                             json=[{"id": tid, "force": True}])

    template_store.unreachable = False
    item = r.json()["results"][0]
    assert item["status"] == "error", item
    assert item["details"]["rule"] == "dependencies_unverifiable"
    assert "force=true" not in item["error"]  # no advice that does not work
    status = (await client.get(f"{API}/terminologies/{tid}", headers=auth_headers)).json()["status"]
    assert status == "active"


@pytest.mark.asyncio
async def test_force_still_overrides_a_known_count(client: AsyncClient, auth_headers: dict, template_store):
    tid = await _terminology(client, auth_headers, "probe_1186_known")
    template_store.templates = [{
        "template_id": "0190c000-0000-7000-0000-000000001186", "value": "t", "label": "t",
        "status": "active", "fields": [{"name": "c", "type": "term", "terminology_ref": tid}],
    }]

    r = await client.request("DELETE", f"{API}/terminologies", headers=auth_headers,
                             json=[{"id": tid, "force": True}])

    assert r.json()["succeeded"] == 1, r.text
    # the check ran, and asked for active templates only
    assert template_store.requests[-1].get("status") == "active"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/health", f"{API}/health"])
async def test_an_unhealthy_def_store_is_not_a_200(client: AsyncClient, path):
    down = {"database": "error", "registry": "connected"}
    with patch("def_store.main._health_cache.get", new=AsyncMock(return_value=down)):
        r = await client.get(path)

    assert r.status_code == 503, r.text
    assert r.json() == {"status": "unhealthy", **down}


@pytest.mark.asyncio
async def test_a_healthy_def_store_is_a_200(client: AsyncClient):
    up = {"database": "connected", "registry": "connected"}
    with patch("def_store.main._health_cache.get", new=AsyncMock(return_value=up)):
        r = await client.get("/health")

    assert r.status_code == 200
    assert r.json()["status"] == "healthy"
