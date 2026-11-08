"""The restore route admin-checks the namespaces the engine will write, and
never skips the check because the archive could not be read (CASE-1109).

The engine writes the manifest's namespaces, or the zip's own namespaces/
tree when the manifest lists none. The route used to check only the
manifest's list, and to skip every check when the manifest failed to parse.
"""

from __future__ import annotations

import contextlib
import io
import json
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from document_store.models.backup_job import BackupJob, BackupJobKind

RESTORE_URL = "/api/document-store/backup/namespaces/wip/restore"
WIP_ADMIN = {"X-API-Key": "test_wip_admin_key"}


@pytest.fixture(autouse=True)
def _isolate_backup_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WIP_BACKUP_DIR", str(tmp_path / "backups"))


@pytest.fixture(autouse=True)
async def _clean_backup_jobs():
    with contextlib.suppress(Exception):
        await BackupJob.delete_all()
    yield
    with contextlib.suppress(Exception):
        await BackupJob.delete_all()


def _archive(manifest: dict, members: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, data in (members or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def _pipeline():
    return (
        patch("document_store.api.backup.backup_service.make_direct_restore_runner",
              return_value=AsyncMock()),
        patch("document_store.api.backup.backup_service.start_async_job",
              new=AsyncMock(return_value=None)),
    )


async def _post(client: AsyncClient, headers: dict, body: bytes, mode: str = "restore"):
    p1, p2 = _pipeline()
    with p1, p2:
        return await client.post(
            RESTORE_URL, headers=headers,
            files={"archive": ("a.zip", body, "application/zip")},
            data={"mode": mode},
        )


async def _restore_jobs() -> int:
    return await BackupJob.find(BackupJob.kind == BackupJobKind.RESTORE).count()


# A manifest that lists no namespace, over a zip that carries one: the engine
# falls back to the zip's tree and writes "other".
TREE_ONLY = _archive(
    {"format_version": "3.1", "namespaces": []},
    {"namespaces/other/terms.jsonl": '{"term_id": "t"}\n'},
)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["restore", "merge"])
async def test_a_tree_only_archive_needs_admin_on_the_tree_namespaces(
    client: AsyncClient, mode: str
):
    resp = await _post(client, WIP_ADMIN, TREE_ONLY, mode)
    assert resp.status_code in (403, 404), resp.text
    assert await _restore_jobs() == 0


@pytest.mark.asyncio
async def test_a_tree_only_archive_restores_its_tree_namespaces_for_an_admin(
    client: AsyncClient, auth_headers: dict
):
    resp = await _post(client, auth_headers, TREE_ONLY)
    assert resp.status_code == 202, resp.text
    assert resp.json()["namespaces"] == ["other"]


@pytest.mark.asyncio
async def test_an_invalid_manifest_is_a_400_not_a_skipped_check(client: AsyncClient):
    body = _archive({"format_version": "3.1", "namespaces": "not-a-list"},
                    {"namespaces/other/terms.jsonl": '{"term_id": "t"}\n'})
    resp = await _post(client, WIP_ADMIN, body)
    assert resp.status_code == 400, resp.text
    assert "not a valid manifest" in resp.json()["detail"]
    assert await _restore_jobs() == 0


@pytest.mark.asyncio
async def test_an_unsafe_member_name_is_a_400(client: AsyncClient, auth_headers: dict):
    body = _archive({"format_version": "3.1", "namespaces": [{"prefix": "wip"}]},
                    {"namespaces/../../evil/terms.jsonl": "{}\n"})
    resp = await _post(client, auth_headers, body)
    assert resp.status_code == 400, resp.text
    assert "segment" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_an_archive_naming_no_namespace_is_a_400(
    client: AsyncClient, auth_headers: dict
):
    resp = await _post(client, auth_headers,
                       _archive({"format_version": "3.1", "namespaces": []}))
    assert resp.status_code == 400, resp.text
    assert "names no namespace" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_an_unexpected_read_error_refuses_instead_of_skipping_the_check(
    client: AsyncClient,
):
    """A transient failure (EMFILE on a busy service) once fell through to
    "no manifest", skipping the admin check, while the engine's read a moment
    later could succeed and write every namespace the archive carries."""
    body = _archive({"format_version": "3.1", "namespaces": [{"prefix": "other"}]})
    with patch("document_store.api.backup.backup_service.read_archive_restore_scope",
               side_effect=OSError(24, "Too many open files")):
        resp = await _post(client, WIP_ADMIN, body)
    assert resp.status_code == 500, resp.text
    assert await _restore_jobs() == 0
