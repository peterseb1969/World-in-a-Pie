"""A Registry that does not answer is never read as "no such document".

Provenance: CASE-1087. A bare document reference resolves through the
Registry's candidates; a failed candidate lookup answered "no candidates",
so the write reported reference_not_found during an outage. The outage now
refuses the item as one: registry_error, rule registry_unavailable — the
per-item code every Registry outage in a bulk document write reports.
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from document_store.services.reference_validator import (
    RegistryUnavailableError as ValidatorRegistryUnavailableError,
)
from wip_auth import RegistryUnavailableError
from wip_auth.registry_client import RegistryClientBase

DOCS = "/api/document-store/documents"


def test_the_service_uses_the_platform_class():
    """One class: an outage raised by wip-auth is caught by the service's handlers."""
    assert ValidatorRegistryUnavailableError is RegistryUnavailableError


@pytest.mark.asyncio
async def test_candidate_lookup_outage_refuses_the_item(client: AsyncClient, auth_headers: dict):
    async def unavailable(*_args, **_kwargs):
        raise RegistryUnavailableError("Registry unavailable (candidate lookup): HTTP 500")

    payload = [{"namespace": "wip", "template_id": "HOLDER_Q",
                "data": {"name": "H-outage", "ref": "Some Title"}}]
    with patch.object(RegistryClientBase, "resolve_candidates", unavailable):
        response = await client.post(DOCS, headers=auth_headers, json=payload)
    assert response.status_code == 200, response.text
    [item] = response.json()["results"]
    assert item["status"] == "error", item
    assert item["error_code"] == "registry_error", item
    assert item["details"] == {"rule": "registry_unavailable"}, item


@pytest.mark.asyncio
async def test_bulk_path_reports_the_same_code(client: AsyncClient, auth_headers: dict):
    async def unavailable(*_args, **_kwargs):
        raise RegistryUnavailableError("Registry unavailable (candidate lookup): HTTP 500")

    payload = [
        {"namespace": "wip", "template_id": "HOLDER_Q", "data": {"name": f"H-out-{n}", "ref": "Some Title"}}
        for n in range(2)
    ]
    with patch.object(RegistryClientBase, "resolve_candidates", unavailable):
        response = await client.post(DOCS, headers=auth_headers, json=payload)
    assert response.status_code == 200, response.text
    for item in response.json()["results"]:
        assert item["status"] == "error", item
        assert item["error_code"] == "registry_error", item
        assert item["details"] == {"rule": "registry_unavailable"}, item
