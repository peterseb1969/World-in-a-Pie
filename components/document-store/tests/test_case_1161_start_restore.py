"""The restore route's refusals, pinned before start_restore's refactor.

Provenance: CASE-1161. These branches of POST /backup/namespaces/{ns}/restore
had no test: namespace_map sent with a mode other than fresh, namespace_map
that is not JSON or not a {source: target} object, an upload that cannot be
written to disk, and a job the runner refuses to start (409).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from document_store.models.backup_job import BackupJob
from tests.test_case_1098_restore_target import RESTORE_URL, _archive, _post_restore


@pytest.mark.asyncio
@pytest.mark.parametrize(("data", "detail"), [
    ({"mode": "restore", "namespace_map": '{"wip": "copy"}'}, "namespace_map applies only to mode='fresh'"),
    ({"mode": "fresh", "namespace_map": "{not json"}, "namespace_map is not valid JSON"),
    ({"mode": "fresh", "namespace_map": '["wip"]'}, "namespace_map must be a non-empty JSON object"),
    ({"mode": "fresh", "namespace_map": "{}"}, "namespace_map must be a non-empty JSON object"),
    ({"mode": "fresh", "namespace_map": '{"wip": ""}'}, "namespace_map must be a non-empty JSON object"),
])
async def test_a_bad_namespace_map_is_refused_before_a_job_exists(
    client: AsyncClient, auth_headers: dict, tmp_path, data, detail,
):
    resp = await _post_restore(client, auth_headers, _archive(tmp_path, ["wip"]), data)
    assert resp.status_code == 400, resp.text
    assert detail in resp.json()["detail"]
    assert await BackupJob.find_one() is None


@pytest.mark.asyncio
async def test_an_upload_that_cannot_be_written_is_a_500_and_leaves_no_file(
    client: AsyncClient, auth_headers: dict, tmp_path,
):
    unwritable = tmp_path / "no-such-dir" / "rst.zip"
    with patch("document_store.api.backup._archive_path_for", return_value=unwritable):
        resp = await _post_restore(client, auth_headers, _archive(tmp_path, ["wip"]), {})
    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"].startswith("Upload failed:")
    assert not unwritable.exists()


@pytest.mark.asyncio
async def test_a_job_the_runner_refuses_is_a_409(
    client: AsyncClient, auth_headers: dict, tmp_path,
):
    archive: Path = _archive(tmp_path, ["wip"])
    with patch(
        "document_store.api.backup.backup_service.make_direct_restore_runner",
        return_value=AsyncMock(),
    ), patch(
        "document_store.api.backup.backup_service.start_async_job",
        new=AsyncMock(side_effect=ValueError("a restore is already running")),
    ):
        resp = await client.post(
            RESTORE_URL, headers=auth_headers,
            files={"archive": ("backup.zip", archive.read_bytes(), "application/zip")},
            data={"mode": "restore"},
        )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "a restore is already running"
    await asyncio.sleep(0)
