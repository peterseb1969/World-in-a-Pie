"""File lows of the batch-13 review: the list filter follows the matcher's
rule, and the upload route admits the Office Open XML types.

- GET /files?content_type= was a second implementation of the type rule: it
  compared case-sensitively and counted a stored type's parameters
  ("; charset=utf-8") as part of it, unlike content_type_matches.
- The Office types were tested at the validator only; nothing posted an
  Office file to the upload route.
- A revival upload's docs said every field it sends replaces the stored one;
  a field sent empty reads as not sent and keeps its value.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_files import _insert_file, auth_headers, file_client  # noqa: F401 — fixtures

FILES = "/api/document-store/files"


@pytest.mark.asyncio
@pytest.mark.parametrize("wanted", ["text/plain", "TEXT/Plain", "text/*", "Text/*"])
async def test_the_list_filter_ignores_case_and_parameters(file_client, auth_headers, wanted):  # noqa: F811
    await _insert_file(content_type="text/plain; charset=utf-8")
    await _insert_file(content_type="Text/Plain")
    await _insert_file(content_type="text/plainx")  # another type: never an exact match
    await _insert_file(content_type="image/png")

    resp = await file_client.get(FILES, headers=auth_headers,
                                 params={"namespace": "wip", "content_type": wanted})

    expected = 3 if wanted.endswith("*") else 2
    assert resp.json()["total"] == expected, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "content_type"), [
    ("a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("a.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("a.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
])
async def test_the_upload_route_admits_office_types(file_client, auth_headers, name, content_type):  # noqa: F811
    registry = MagicMock()
    registry.status_code = 200
    registry.json.return_value = {"results": [{"status": "created", "registry_id": str(uuid.uuid4())}]}
    with patch("document_store.services.file_service.httpx.AsyncClient") as http:
        client = AsyncMock()
        client.post = AsyncMock(return_value=registry)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        http.return_value = client
        resp = await file_client.post(
            FILES, headers=auth_headers,
            files={"file": (name, uuid.uuid4().bytes, content_type)},
            data={"namespace": "wip"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["content_type"] == content_type



@pytest.mark.asyncio
async def test_a_revival_upload_sending_empty_tags_keeps_them(file_client, auth_headers):  # noqa: F811
    # An empty form field reads as not sent (FastAPI's Form default), so the
    # upload cannot clear a field; the docstring and the docs say so.
    from document_store.models.file import File
    from tests.test_case_1154_file_revival import _deleted, _upload

    deleted = await _deleted(tags=["old"], description="old")
    resp = await _upload(file_client, auth_headers, deleted.file_id, tags="", description="")
    assert resp.status_code == 200, resp.text
    stored = await File.find_one({"file_id": deleted.file_id})
    assert (stored.metadata.tags, stored.metadata.description) == (["old"], "old")
