"""A merge honors the caller's target_namespace (CASE-748).

The engine's redirected merge — namespace rewrite, composite-key rehash,
ids-must-be-free refusal — was fully implemented and fully tested at engine
level, and unreachable from the API: `_authorize_archive_restore` resolves
the target from the MANIFEST (correct for restore mode) and overwrote the
caller's value for merge too. The observable damage was worse than a missing
feature: "merge into scratch-ns" executed against the live SOURCE namespace
the caller never named (with on_clash=skip that was a benign self-merge; with
overwrite it would have mutated live data).

These pin the route seam that was missing — the suite proved "the engine can
redirect," not "a caller can."
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


def _make_v3_archive(tmp_path, prefixes: list[str]):
    """A real v3 zip carrying `prefixes` as its namespaces list + subtrees."""
    path = tmp_path / f"archive-{'-'.join(prefixes)}.zip"
    manifest = {
        "format_version": "3.0",
        "created_at": "2026-07-21T00:00:00",
        "namespaces": [{"prefix": p} for p in prefixes],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for p in prefixes:
            zf.writestr(f"namespaces/{p}/terminologies.jsonl", "")
    return path


def _patched_restore():
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


async def _post_merge(client, auth_headers, archive, data):
    p1, p2 = _patched_restore()
    with p1, p2:
        return await client.post(
            RESTORE_URL,
            headers=auth_headers,
            files={"archive": ("backup.zip", archive.read_bytes(), "application/zip")},
            data={"mode": "merge", **data},
        )


@pytest.mark.asyncio
async def test_redirected_merge_carries_the_callers_target(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    """The regression assert is the recorded options: the live incident's
    job carried options.target_namespace == "" after the caller sent a
    real value."""
    archive = _make_v3_archive(tmp_path, ["wip"])

    resp = await _post_merge(
        client, auth_headers, archive, {"target_namespace": "scratch-ns"}
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["options"]["target_namespace"] == "scratch-ns"
    # The job records the WRITE target — sync and validation scope from it.
    assert body["namespace"] == "scratch-ns"
    assert body["namespaces"] == ["scratch-ns"]

    stored = await BackupJob.find_one(BackupJob.job_id == body["job_id"])
    assert stored is not None
    assert stored.options["target_namespace"] == "scratch-ns"


@pytest.mark.asyncio
async def test_multi_namespace_archive_with_target_is_a_loud_400(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    """The engine would refuse this after a job exists; the route refuses
    before one does. The live incident accepted it and ran a REAL self-merge
    of the two live source namespaces."""
    archive = _make_v3_archive(tmp_path, ["kb", "library"])

    resp = await _post_merge(
        client, auth_headers, archive, {"target_namespace": "scratch-ns"}
    )

    assert resp.status_code == 400, resp.text
    assert "multi-namespace archive" in resp.json()["detail"]
    assert await BackupJob.find_one() is None  # no job minted


@pytest.mark.asyncio
async def test_merge_without_target_still_merges_each_namespace_to_itself(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    """The self-merge default is correct and must survive the fix."""
    archive = _make_v3_archive(tmp_path, ["kb", "library"])

    resp = await _post_merge(client, auth_headers, archive, {})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["options"]["target_namespace"] == ""
    assert body["namespaces"] == ["kb", "library"]


@pytest.mark.asyncio
async def test_single_namespace_merge_without_target_targets_the_source(
    client: AsyncClient, auth_headers: dict, tmp_path
):
    archive = _make_v3_archive(tmp_path, ["kb"])

    resp = await _post_merge(client, auth_headers, archive, {})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["options"]["target_namespace"] == "kb"
    assert body["namespaces"] == ["kb"]
