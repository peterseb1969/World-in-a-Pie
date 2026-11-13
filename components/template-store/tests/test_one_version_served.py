"""The service serves one version: its package __version__, in OpenAPI and at
its root, build block included. Nothing else pinned it, so a version bumped in
one place and not the others went unnoticed."""

import pytest

from template_store import __version__
from template_store.main import app


@pytest.mark.asyncio
async def test_openapi_and_root_serve_the_package_version(client):
    assert app.openapi()["info"]["version"] == __version__
    r = await client.get("/api/template-store/")
    assert r.status_code == 200, r.text
    root = r.json()
    assert root["version"] == __version__
    assert root["build"]["version"] == __version__
