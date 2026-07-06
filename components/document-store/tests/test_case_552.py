"""Restoring a pre-v3 archive must fail loud, not create an empty namespace.

A v2 archive is flat (entity JSONL at the zip root); the v3 restore loop reads
``namespaces/<prefix>/<entity>.jsonl``, so against a v2 zip every read finds
nothing — the engine would create the target namespace, restore zero entities,
and report success. Both layers now refuse instead:

- the endpoint returns a synchronous 400 before any job or namespace exists;
- the engine fails the job for callers that bypass the endpoint, including the
  pathological "manifest claims 3.x but the zip has no namespaces/ tree" case.
"""

import json
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from document_store.services.backup_engine import (
    DirectRestoreEngine,
    RestoreEngineError,
)

RESTORE_URL = "/api/document-store/backup/namespaces/wip/restore"


def _make_archive(tmp_path, *, format_version: str, with_v3_tree: bool):
    """A minimal real zip: manifest + either flat (v2) or namespaces/ layout."""
    path = tmp_path / f"archive-{format_version}-{with_v3_tree}.zip"
    manifest = {
        "format_version": format_version,
        "namespace": "wip",
        "created_at": "2026-07-06T00:00:00",
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        if with_v3_tree:
            zf.writestr("namespaces/wip/terminologies.jsonl", "")
        else:
            zf.writestr("terminologies.jsonl", "")
    return path


class TestEndpointRejectsPreV3:
    @pytest.mark.asyncio
    async def test_v2_archive_gets_400_and_no_job(
        self, client: AsyncClient, auth_headers: dict, tmp_path
    ):
        archive = _make_archive(tmp_path, format_version="2.0", with_v3_tree=False)

        resp = await client.post(
            RESTORE_URL,
            headers=auth_headers,
            files={"archive": ("old.zip", archive.read_bytes(), "application/zip")},
            data={"mode": "restore"},
        )

        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert "v2.0" in detail
        assert "convert-archive" in detail

    @pytest.mark.asyncio
    async def test_v3_archive_still_accepted(
        self, client: AsyncClient, auth_headers: dict, tmp_path
    ):
        archive = _make_archive(tmp_path, format_version="3.0", with_v3_tree=True)
        import asyncio

        fake_task = asyncio.get_running_loop().create_future()
        fake_task.set_result(None)

        with (
            patch(
                "document_store.api.backup.backup_service.make_direct_restore_runner",
                return_value=AsyncMock(),
            ),
            patch(
                "document_store.api.backup.backup_service.start_async_job",
                new=AsyncMock(return_value=fake_task),
            ),
        ):
            resp = await client.post(
                RESTORE_URL,
                headers=auth_headers,
                files={"archive": ("new.zip", archive.read_bytes(), "application/zip")},
                data={"mode": "restore"},
            )

        assert resp.status_code == 202, resp.text


class TestEngineRefusesPreV3:
    def _engine(self):
        return DirectRestoreEngine(MagicMock(), None, lambda _: None)

    @pytest.mark.asyncio
    async def test_v2_archive_raises_with_conversion_hint(self, tmp_path):
        archive = _make_archive(tmp_path, format_version="2.0", with_v3_tree=False)
        with pytest.raises(RestoreEngineError, match=r"v2\.0.*convert-archive"):
            await self._engine().run_restore(archive, "wip")

    @pytest.mark.asyncio
    async def test_v3_manifest_with_flat_layout_raises(self, tmp_path):
        # Manifest lies (or the zip is truncated): claims 3.x, no namespaces/
        # tree. namespace_prefixes() is non-empty from the manifest alone, so
        # without the layout check this would restore zero entities silently.
        archive = _make_archive(tmp_path, format_version="3.0", with_v3_tree=False)
        with pytest.raises(RestoreEngineError, match="no namespaces/ tree"):
            await self._engine().run_restore(archive, "wip")
