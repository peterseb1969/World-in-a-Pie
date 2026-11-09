"""An unhealthy document-store does not answer 200 on /health.

With the database unreachable, /health answers 503 with its usual body, so a
probe reading only the status code (the image's HEALTHCHECK, curl -f;
WIP-Toolkit's `wip status`) does not see a healthy service. Orchestrators
probe /ready and /livez, which this does not change.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/health", "/api/document-store/health"])
async def test_an_unhealthy_document_store_is_not_a_200(client: AsyncClient, path):
    down = {"database": "error", "registry": "connected", "template_store": "connected", "def_store": "connected"}
    with patch("document_store.main._health_cache.get", new=AsyncMock(return_value=down)):
        r = await client.get(path)

    assert r.status_code == 503, r.text
    body = r.json()
    assert body["status"] == "unhealthy"
    assert body["database"] == "error"


@pytest.mark.asyncio
async def test_a_healthy_document_store_is_a_200(client: AsyncClient):
    up = {"database": "connected", "registry": "connected", "template_store": "connected", "def_store": "connected"}
    with patch("document_store.main._health_cache.get", new=AsyncMock(return_value=up)):
        r = await client.get("/health")

    assert r.status_code == 200
    assert r.json()["status"] == "healthy"
