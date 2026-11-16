"""File-ID minting reads a Registry that answers 5xx as an outage, as
document-store's other Registry calls do since 82c24d26.

`POST /files` mints its id with `/entries/register` from file_service's own
call. A 5xx there raised a plain RegistryError, which the route answered
502 with no rule, so a client could not tell the Registry's restart or
overload from a refusal. The service error now carries details.rule
registry_unavailable and the route answers 503, as for an unreachable
Registry; a 4xx stays a refusal (502, no rule).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from .test_files import auth_headers, file_client  # noqa: F401  (fixtures)


def _registry_answering(status: int):
    response = MagicMock()
    response.status_code = status
    response.text = "upstream said no"
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


async def _upload(file_client: AsyncClient, headers: dict, status: int):  # noqa: F811
    with patch("document_store.services.file_service.httpx.AsyncClient") as http:
        http.return_value = _registry_answering(status)
        return await file_client.post(
            "/api/document-store/files", headers=headers,
            files={"file": ("a.txt", b"bytes", "text/plain")}, data={"namespace": "wip"},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 503])
async def test_a_registry_5xx_is_an_outage(file_client, auth_headers, status):  # noqa: F811
    r = await _upload(file_client, auth_headers, status)
    assert r.status_code == 503, r.text
    assert "did not answer" in r.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "rule"), [(503, "registry_unavailable"), (422, None)])
async def test_the_service_error_carries_the_outage_rule(file_client, status, rule):  # noqa: F811
    from document_store.services.file_service import FileServiceError, get_file_service

    with patch("document_store.services.file_service.httpx.AsyncClient") as http:
        http.return_value = _registry_answering(status)
        with pytest.raises(FileServiceError) as refused:
            await get_file_service().upload_file(b"bytes", "a.txt", "text/plain", "wip")
    assert refused.value.code == "registry_error"
    assert (refused.value.details or {}).get("rule") == rule


@pytest.mark.asyncio
async def test_a_registry_4xx_stays_a_refusal(file_client, auth_headers):  # noqa: F811
    r = await _upload(file_client, auth_headers, 422)
    assert r.status_code == 502, r.text
    assert "registry_unavailable" not in r.text
