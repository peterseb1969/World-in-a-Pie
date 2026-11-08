"""The Registry's namespace export/import is retired.

Its import posted one object per request to list-only store endpoints, so it
restored only the namespace record and reported success; its export broke off
silently on a failed page. Namespace backup and restore is the document-store
backup/restore engine. Provenance: CASE-1123.
"""

import pytest


@pytest.mark.asyncio
async def test_the_routes_are_gone(client):
    paths = (await client.get("/openapi.json")).json()["paths"]
    for retired in (
        "/api/registry/namespaces/{prefix}/export",
        "/api/registry/namespaces/exports/{export_id}",
        "/api/registry/namespaces/import",
    ):
        assert retired not in paths
