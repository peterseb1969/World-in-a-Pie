"""A door whose `namespace` is omittable takes the one namespace a key's scope
implies beside a read-only `wip`.

Provenance: CASE-512. A deployer-derived app key is scoped to the app's
namespace plus `wip` (read), so it can read two namespaces and the doors that
imply "the one namespace a key can read" implied nothing for it. A key that
may write `wip` as well still implies nothing.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

FACETS = "/api/document-store/documents/template-facets"
APP_SHAPED = {"X-API-Key": "test_app_shaped_key"}
# Scoped to 'wip' (write) and 'xns-closed' (read): two namespaces, one of
# them a writable 'wip'.
WIP_WRITER = {"X-API-Key": "test_xns_reader_key"}


@pytest.mark.asyncio
async def test_an_app_shaped_key_has_its_own_namespace_implied(client: AsyncClient):
    resp = await client.get(FACETS, headers=APP_SHAPED)
    assert resp.status_code == 200, resp.text
    assert resp.json()["namespace"] == "xns-closed"


@pytest.mark.asyncio
async def test_a_key_that_may_write_wip_must_name_the_namespace(client: AsyncClient):
    resp = await client.get(FACETS, headers=WIP_WRITER)
    assert resp.status_code == 422, resp.text
    assert "namespace is required" in resp.json()["detail"]
