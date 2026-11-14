"""Batch-13 review lows on the restore route (d6cc8484).

- A manifest prefix became a URL path segment of the Registry calls the
  restore makes, with no segment rule: "../x" reached a path the admin check
  never named, and an empty prefix passed the "names no namespace" gate and
  restored nothing. Each prefix must now be one path segment (400).
- An archive corrupt below the JSON layer answered 500; it is a malformed
  upload (400).
- The 500 for an archive the service could not read echoed the exception
  text, scratch paths included; it is a fixed message now.
"""

import json
import zipfile
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from document_store.services import backup_service

RESTORE_URL = "/api/document-store/backup/namespaces/wip/restore"


def _archive(tmp_path, manifest, members=None) -> bytes:
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", manifest if isinstance(manifest, bytes) else json.dumps(manifest))
        for name, data in (members or {}).items():
            zf.writestr(name, data)
    return path.read_bytes()


async def _restore(client, headers, data: bytes):
    return await client.post(
        RESTORE_URL, headers=headers,
        files={"archive": ("a.zip", data, "application/zip")},
        data={"mode": "restore"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["../x", "", "a/b", "C:x"])
async def test_a_prefix_that_is_not_one_segment_is_refused(
    client: AsyncClient, auth_headers, tmp_path, prefix,
):
    data = _archive(tmp_path, {
        "format_version": "3.1", "counts": {},
        "namespaces": [{"prefix": prefix, "counts": {}}],
    })
    resp = await _restore(client, auth_headers, data)
    assert resp.status_code == 400, resp.text
    assert "path" in resp.json()["detail"], resp.text


@pytest.mark.asyncio
async def test_a_manifest_that_is_not_utf8_is_a_400(client: AsyncClient, auth_headers, tmp_path):
    resp = await _restore(client, auth_headers, _archive(tmp_path, b'{"format_version": "3.1\xff"}'))
    assert resp.status_code == 400, resp.text


@pytest.mark.asyncio
async def test_an_unreadable_archive_answers_a_fixed_500(client: AsyncClient, auth_headers, tmp_path):
    def unreadable(path):
        raise OSError(f"[Errno 24] Too many open files: '{path}'")

    with patch.object(backup_service, "read_archive_restore_scope", unreadable):
        resp = await _restore(client, auth_headers, _archive(tmp_path, {"format_version": "3.1"}))
    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert "Errno" not in detail and ".zip" not in detail, detail
