"""A plain restore honours or refuses target_namespace, never replaces it.

Provenance: CASE-1098. The upload restore resolved its destination from the
archive manifest and silently dropped the caller's target_namespace in
restore mode, so a request naming 'kb-copy' wrote into the archive's own 'kb'
(when it was absent or empty), and the engine's refusal of a re-namespaced
restore was unreachable through the API. Now a differing target is a 400
before any job exists; the archive's own name is accepted as before.

Mirrors test_case_748, which pins the merge side of the same seam.
"""

from __future__ import annotations

import asyncio
import json
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from document_store.models.backup_job import BackupJob

RESTORE_URL = "/api/document-store/backup/namespaces/wip/restore"


def _archive(tmp_path, prefixes: list[str]):
    path = tmp_path / f"archive-{'-'.join(prefixes)}.zip"
    manifest = {
        "format_version": "3.0",
        "created_at": "2026-11-08T00:00:00",
        "namespaces": [{"prefix": p} for p in prefixes],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for p in prefixes:
            zf.writestr(f"namespaces/{p}/terminologies.jsonl", "")
    return path


async def _post_restore(client, auth_headers, archive, data):
    done = asyncio.get_running_loop().create_future()
    done.set_result(None)
    with patch(
        "document_store.api.backup.backup_service.make_direct_restore_runner",
        return_value=AsyncMock(),
    ), patch(
        "document_store.api.backup.backup_service.start_async_job",
        new=AsyncMock(return_value=done),
    ):
        return await client.post(
            RESTORE_URL,
            headers=auth_headers,
            files={"archive": ("backup.zip", archive.read_bytes(), "application/zip")},
            data={"mode": "restore", **data},
        )


@pytest.mark.asyncio
async def test_a_differing_target_is_refused_before_a_job_exists(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    resp = await _post_restore(
        client, auth_headers, _archive(tmp_path, ["wip"]), {"target_namespace": "wip-copy"}
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "'wip-copy' differs from the archive's namespace 'wip'" in detail
    assert "mode=merge" in detail and "mode=fresh" in detail
    assert await BackupJob.find_one() is None


@pytest.mark.asyncio
async def test_the_archives_own_namespace_is_accepted(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    resp = await _post_restore(
        client, auth_headers, _archive(tmp_path, ["wip"]), {"target_namespace": "wip"}
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["options"]["target_namespace"] == "wip"


@pytest.mark.asyncio
async def test_no_target_still_restores_to_the_archives_namespace(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    resp = await _post_restore(client, auth_headers, _archive(tmp_path, ["wip"]), {})
    assert resp.status_code == 202, resp.text
    assert resp.json()["options"]["target_namespace"] == "wip"


@pytest.mark.asyncio
async def test_a_multi_namespace_archive_with_a_target_is_refused(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    resp = await _post_restore(
        client, auth_headers, _archive(tmp_path, ["kb", "library"]), {"target_namespace": "kb"}
    )
    assert resp.status_code == 400, resp.text
    assert "multi-namespace archive in restore mode" in resp.json()["detail"]
    assert await BackupJob.find_one() is None
