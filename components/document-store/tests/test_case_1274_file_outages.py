"""A bulk file write answers an outage on the item it hit.

PATCH, DELETE and POST /files/reactivate caught only FileServiceError per
item. A Registry outage while resolving a PATCH's allowed_templates, or an
unreachable object store while a reactivate checked a file's bytes, escaped
the loop: the items before it were already written, and the request answered
503 with no per-item report. Each such item now reports
dependency_unavailable (registry_error with details.rule registry_unavailable
for the Registry) and the batch goes on.
"""

from unittest.mock import patch

import pytest

from document_store.services.file_storage_client import FileStorageUnavailableError
from tests.test_case_1154_file_revival import REACTIVATE, _deleted, _storage
from tests.test_files import _insert_file, auth_headers, file_client  # noqa: F401 — fixtures
from wip_auth.registry_client import RegistryUnavailableError

FILES = "/api/document-store/files"


@pytest.mark.asyncio
async def test_patch_reports_a_registry_outage_per_item(file_client, auth_headers):  # noqa: F811
    first = await _insert_file(checksum="1274a", filename="a.txt", content_type="text/plain")
    second = await _insert_file(checksum="1274b", filename="b.txt", content_type="text/plain")
    calls = 0

    async def resolve(entries, namespace):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RegistryUnavailableError("registry down")
        return None

    with patch("document_store.services.file_service.resolve_allowed_templates", resolve):
        resp = await file_client.patch(FILES, headers=auth_headers, json=[
            {"file_id": first.file_id, "allowed_templates": ["PERSON"]},
            {"file_id": second.file_id, "allowed_templates": ["PERSON"]},
        ])
    assert resp.status_code == 200, resp.text
    one, two = resp.json()["results"]
    assert one["status"] == "error", one
    assert one["details"]["rule"] == "registry_unavailable", one
    assert two["status"] == "updated", two


@pytest.mark.asyncio
async def test_reactivate_reports_an_object_store_outage_per_item(file_client, auth_headers):  # noqa: F811
    first = await _deleted()
    second = await _insert_file(checksum="1274c", filename="c.txt", content_type="text/plain",
                                status=first.status)
    calls = 0
    real = _storage().exists

    async def exists(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise FileStorageUnavailableError("object store unreachable")
        return await real(*args, **kwargs)

    _storage().exists = exists
    try:
        resp = await file_client.post(REACTIVATE, headers=auth_headers, json=[
            {"id": first.file_id}, {"id": second.file_id},
        ])
    finally:
        _storage().exists = real
    assert resp.status_code == 200, resp.text
    one, two = resp.json()["results"]
    assert one["status"] == "error", one
    assert one["error_code"] == "dependency_unavailable", one
    assert two["status"] == "reactivated", two
