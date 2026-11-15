"""A bulk file write answers an outage on the item it hit.

PATCH, DELETE and POST /files/reactivate caught only FileServiceError per
item. A Registry outage while resolving a PATCH's allowed_templates, or an
unreachable object store while a reactivate checked a file's bytes, escaped
the loop: the items before it were already written, and the request answered
503 with no per-item report. Each such item now reports
dependency_unavailable (registry_error with details.rule registry_unavailable
for the Registry) and the batch goes on.

A reactivate batch reaches the object store once per inactive file, so an
outage there used to be waited out once per file as well. The batch now asks
the store once: after an item meets it, the later inactive items report the
same outage without being asked, and the items that need no storage keep
their own answer.
"""

from unittest.mock import patch

import pytest

from document_store.models.file import File, FileStatus
from document_store.services.file_storage_client import FileStorageUnavailableError
from tests.test_case_1154_file_revival import REACTIVATE, _storage
from tests.test_files import _insert_file, auth_headers, file_client  # noqa: F401 — fixtures
from wip_auth.registry_client import RegistryUnavailableError

FILES = "/api/document-store/files"


async def _inactive(checksum: str) -> File:
    """A soft-deleted file, its bytes still in the store."""
    return await _insert_file(checksum=checksum, filename=f"{checksum}.txt",
                              content_type="text/plain", status=FileStatus.INACTIVE)


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
async def test_reactivate_meets_an_object_store_outage_once_for_the_batch(file_client, auth_headers):  # noqa: F811
    """Every inactive item still reports the outage, but the store is asked
    once: the later items are refused from the outage already met, while the
    items that need no storage answer on their own merits."""
    inactive = [await _inactive(f"1323a{i}") for i in range(3)]
    live = await _insert_file(checksum="1323live", filename="live.txt",
                             content_type="text/plain", status=FileStatus.ACTIVE)
    gone_id = "019d0000-0000-7000-8000-000000001323"
    calls = 0
    real = _storage().exists

    async def exists(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise FileStorageUnavailableError("object store unreachable")

    _storage().exists = exists
    try:
        resp = await file_client.post(REACTIVATE, headers=auth_headers, json=[
            {"id": f.file_id} for f in inactive
        ] + [{"id": live.file_id}, {"id": gone_id}])
    finally:
        _storage().exists = real

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert calls == 1, f"the store was asked {calls} times for one outage"
    assert body["failed"] == 4 and body["succeeded"] == 1, body
    for result in body["results"][:3]:
        assert result["status"] == "error", result
        assert result["error_code"] == "dependency_unavailable", result
        assert result["details"]["dependency"] == "object-store", result
    assert body["results"][3]["status"] == "unchanged", body["results"][3]
    assert body["results"][4]["error_code"] == "not_found", body["results"][4]
    for f in inactive:
        assert (await File.find_one({"file_id": f.file_id})).status == FileStatus.INACTIVE


@pytest.mark.asyncio
async def test_reactivate_keeps_what_it_answered_before_an_outage(file_client, auth_headers):  # noqa: F811
    """The items already reactivated stay reported as such, and the outage
    stops the asking rather than the request: no whole-request 503."""
    done = await _inactive("1323b1")
    after = [await _inactive(f"1323b{i}") for i in (2, 3)]
    calls = 0
    real = _storage().exists

    async def exists(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise FileStorageUnavailableError("object store unreachable")
        return await real(*args, **kwargs)

    _storage().exists = exists
    try:
        resp = await file_client.post(REACTIVATE, headers=auth_headers, json=[
            {"id": done.file_id}, *[{"id": f.file_id} for f in after],
        ])
    finally:
        _storage().exists = real

    assert resp.status_code == 200, resp.text
    first, second, third = resp.json()["results"]
    assert calls == 2, f"the store was asked {calls} times for one outage"
    assert first["status"] == "reactivated", first
    for result in (second, third):
        assert result["status"] == "error" and result["error_code"] == "dependency_unavailable", result
    assert (await File.find_one({"file_id": done.file_id})).status == FileStatus.ORPHAN
    for f in after:
        assert (await File.find_one({"file_id": f.file_id})).status == FileStatus.INACTIVE


@pytest.mark.asyncio
async def test_reactivate_a_file_whose_bytes_are_gone_does_not_stand_down_the_batch(
    file_client, auth_headers,  # noqa: F811
):
    """`storage_missing` is an answer about that file, not an outage: the store
    keeps being asked for the items after it."""
    gone = await _inactive("1323c1")
    present = await _inactive("1323c2")
    _storage().exists.side_effect = [False, True]
    try:
        resp = await file_client.post(REACTIVATE, headers=auth_headers, json=[
            {"id": gone.file_id}, {"id": present.file_id},
        ])
    finally:
        _storage().exists.side_effect = None

    assert resp.status_code == 200, resp.text
    first, second = resp.json()["results"]
    assert first["error_code"] == "invalid_state" and first["details"]["rule"] == "storage_missing", first
    assert second["status"] == "reactivated", second
    assert _storage().exists.await_count == 2
