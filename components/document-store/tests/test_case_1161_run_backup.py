"""Backup pipeline branches the suite did not reach, pinned before
run_backup's refactor.

Provenance: CASE-1161. An empty namespace list is refused; a long entity
read reports progress every 5000 rows; file blobs are backed up per
namespace only when asked and a storage client is present; a failure cleans
the writer's temp files and propagates. The end-to-end shape (counts,
manifest, referents) is covered by test_backup_engine.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from document_store.services.backup_engine import (
    BACKUP_ENTITY_ORDER,
    BackupEngineError,
    DirectBackupEngine,
)
from tests.test_backup_engine import _collect_progress, _make_mongo_mock


def _mongo(documents=()):
    mongo, _ = _make_mongo_mock(
        docs_per_collection={"documents": list(documents)},
        counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER} | {"documents": len(documents)},
        namespace_config_doc={"prefix": "kb"},
    )
    return mongo


@pytest.mark.asyncio
async def test_an_empty_namespace_list_is_refused(tmp_path):
    engine = DirectBackupEngine(_mongo(), None, lambda _: None)
    with pytest.raises(BackupEngineError, match="at least one namespace"):
        await engine.run_backup([], tmp_path / "a.zip")


@pytest.mark.asyncio
async def test_a_long_read_reports_progress_every_5000_rows(tmp_path):
    docs = [{"document_id": f"D{i}", "namespace": "kb", "template_id": "T"} for i in range(10_001)]
    events: list = []
    engine = DirectBackupEngine(_mongo(docs), None, _collect_progress(events))
    await engine.run_backup("kb", tmp_path / "a.zip")
    ticks = [(e.message, e.current, e.total) for e in events if e.phase == "phase_documents" and e.current]
    assert ticks == [
        ("[kb] documents: 5000/10001", 5000, 10001),
        ("[kb] documents: 10000/10001", 10000, 10001),
    ]
    assert events[-1].phase == "complete"


@pytest.mark.asyncio
@pytest.mark.parametrize(("include_files", "storage", "called"), [
    (True, object(), True), (True, None, False), (False, object(), False),
])
async def test_blobs_are_backed_up_per_namespace_only_when_asked(tmp_path, include_files, storage, called):
    engine = DirectBackupEngine(_mongo(), storage, lambda _: None)
    archive = tmp_path / "a.zip"
    with patch.object(DirectBackupEngine, "_backup_blobs", new=AsyncMock()) as blobs:
        await engine.run_backup(["kb", "kb2"], archive, include_files=include_files)
    if called:
        assert [c.args[1:3] for c in blobs.await_args_list] == [("kb", archive), ("kb2", archive)]
    else:
        blobs.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failure_cleans_the_writer_and_propagates(tmp_path):
    engine = DirectBackupEngine(_mongo(), None, lambda _: None)
    with (
        patch("document_store.services.backup_engine.ArchiveWriter.write", side_effect=OSError("disk full")),
        patch("document_store.services.backup_engine.ArchiveWriter._cleanup") as cleanup,
        pytest.raises(OSError, match="disk full"),
    ):
        await engine.run_backup("kb", tmp_path / "a.zip")
    cleanup.assert_called()


def _docs(n):
    return [{"document_id": f"D{i}", "namespace": "kb", "template_id": "T"} for i in range(n)]


@pytest.mark.asyncio
async def test_entities_created_while_the_backup_reads_them_are_a_warning(tmp_path):
    # Two documents counted before the read, three read: one was created
    # while the backup ran, and the archive may reference what it lacks.
    mongo, _ = _make_mongo_mock(
        docs_per_collection={"documents": _docs(3)},
        counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER} | {"documents": 2},
        namespace_config_doc={"prefix": "kb"},
    )
    events: list = []
    await DirectBackupEngine(mongo, None, _collect_progress(events)).run_backup("kb", tmp_path / "a.zip")
    warnings = [e.message for e in events if e.phase == "warning"]
    assert len(warnings) == 1
    assert "kb documents: 2 before, 3 read, 2 after" in warnings[0]
    assert events[-1].phase == "complete"


@pytest.mark.asyncio
async def test_a_backup_whose_counts_held_still_warns_of_nothing(tmp_path):
    events: list = []
    await DirectBackupEngine(_mongo(_docs(3)), None, _collect_progress(events)).run_backup(
        "kb", tmp_path / "a.zip",
    )
    assert [e for e in events if e.phase == "warning"] == []


@pytest.mark.asyncio
async def test_the_manifest_names_the_engine_that_wrote_it(tmp_path, monkeypatch):
    from wip_archive.archive import ArchiveReader

    from document_store import __version__

    monkeypatch.setenv("WIP_IMAGE_TAG", "v9.9.9")
    monkeypatch.setenv("WIP_BUILD_SHA", "abc1234")
    archive = tmp_path / "a.zip"
    await DirectBackupEngine(_mongo(), None, lambda _: None).run_backup("kb", archive)
    with ArchiveReader(archive) as reader:
        tool_version = reader.read_manifest().tool_version
    assert tool_version == f"document-store {__version__} (image v9.9.9, build abc1234)"
