"""template-store's own app enforces the rate limit on its API routes.

The limit is installed by wip_auth.ratelimit; this pins it on a real service
app, whose routes come from an included router (where slowapi's own route
lookup finds nothing and exempts the request) and whose middleware order is
the service's: auth, then the limit, then query checks and CORS.
"""

import pytest
from httpx import AsyncClient

URL = "/api/template-store/templates"


@pytest.mark.asyncio
async def test_a_request_through_the_proxy_is_counted(client: AsyncClient, auth_headers):
    resp = await client.get(URL, params={"namespace": "wip"},
                            headers={**auth_headers, "X-Forwarded-For": "203.0.113.70"})
    assert resp.status_code == 200, resp.text
    assert int(resp.headers["X-RateLimit-Limit"]) > 0
    assert "X-RateLimit-Remaining" in resp.headers


@pytest.mark.asyncio
async def test_an_internal_call_is_not_counted(client: AsyncClient, auth_headers):
    resp = await client.get(URL, params={"namespace": "wip"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert "X-RateLimit-Limit" not in resp.headers
