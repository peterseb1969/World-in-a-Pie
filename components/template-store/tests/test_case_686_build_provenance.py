"""The router preserves the /api/template-store prefix, so the root payload (with its
build-provenance block) must also be served at the prefixed path — otherwise
router-side consumers (the console's build probe at /api/template-store/) get a 404 and
the health dashboard shows no build info for this service."""

import pytest
from httpx import AsyncClient

from template_store import __version__


@pytest.mark.asyncio
async def test_prefixed_root_serves_build_provenance(client: AsyncClient):
    response = await client.get("/api/template-store/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "WIP Template Store"
    build = data["build"]
    assert set(build) == {"version", "sha", "built_at", "image_tag"}
    # version comes from the package, not a hand-maintained literal
    assert build["version"] == __version__
