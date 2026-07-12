"""A restore job's snapshot must carry the namespaces it restores (CASE-547).

`start_restore` reads the archive manifest's namespace set for its per-namespace
admin checks and to pick single-vs-multi target, then used to discard it — the
restore `BackupJob` was created with no `namespaces`, so the snapshot returned
and polled by clients showed an empty list (and, for a multi-namespace archive,
`namespace` = the URL anchor). A UI could not enumerate what was being restored.

The endpoint now populates `namespaces` from the manifest prefixes it already
reads. For a multi-namespace archive `namespace` stays the URL anchor (each
namespace restores into itself) while `namespaces` carries the actual set.
"""

import asyncio
import json
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

RESTORE_URL = "/api/document-store/backup/namespaces/wip/restore"


def _make_v3_archive(tmp_path, prefixes: list[str]):
    """A real v3 zip carrying `prefixes` as its namespaces list + subtrees."""
    path = tmp_path / f"archive-{'-'.join(prefixes)}.zip"
    manifest = {
        "format_version": "3.0",
        "created_at": "2026-07-06T00:00:00",
        "namespaces": [{"prefix": p} for p in prefixes],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for p in prefixes:
            zf.writestr(f"namespaces/{p}/terminologies.jsonl", "")
    return path


def _patched_restore():
    """Patch the async runner so no real restore executes."""
    fake_task = asyncio.get_running_loop().create_future()
    fake_task.set_result(None)
    return (
        patch(
            "document_store.api.backup.backup_service.make_direct_restore_runner",
            return_value=AsyncMock(),
        ),
        patch(
            "document_store.api.backup.backup_service.start_async_job",
            new=AsyncMock(return_value=fake_task),
        ),
    )


@pytest.mark.asyncio
async def test_single_namespace_restore_populates_namespaces(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    archive = _make_v3_archive(tmp_path, ["wip"])
    runner_patch, job_patch = _patched_restore()
    with runner_patch, job_patch:
        resp = await client.post(
            RESTORE_URL,
            headers=auth_headers,
            files={"archive": ("one.zip", archive.read_bytes(), "application/zip")},
            data={"mode": "restore"},
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    # Single namespace: it is both the target anchor and the restored set.
    assert body["namespace"] == "wip"
    assert body["namespaces"] == ["wip"]


@pytest.mark.asyncio
async def test_multi_namespace_restore_carries_full_set(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    archive = _make_v3_archive(tmp_path, ["alpha", "beta"])
    runner_patch, job_patch = _patched_restore()
    with runner_patch, job_patch:
        resp = await client.post(
            RESTORE_URL,
            headers=auth_headers,
            files={"archive": ("multi.zip", archive.read_bytes(), "application/zip")},
            data={"mode": "restore"},
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    # Multi-namespace: `namespace` is only the URL auth anchor (each namespace
    # restores into itself); `namespaces` is the real restored set — the whole
    # point of the case.
    assert body["namespace"] == "wip"
    assert body["namespaces"] == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_restore_snapshot_persists_namespaces(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    """The populated list survives to the stored job, not just the 202 body."""
    from document_store.models.backup_job import BackupJob

    archive = _make_v3_archive(tmp_path, ["alpha", "beta"])
    runner_patch, job_patch = _patched_restore()
    with runner_patch, job_patch:
        resp = await client.post(
            RESTORE_URL,
            headers=auth_headers,
            files={"archive": ("multi.zip", archive.read_bytes(), "application/zip")},
            data={"mode": "restore"},
        )

    assert resp.status_code == 202, resp.text
    stored = await BackupJob.find_one(BackupJob.job_id == resp.json()["job_id"])
    assert stored is not None
    assert stored.namespaces == ["alpha", "beta"]
