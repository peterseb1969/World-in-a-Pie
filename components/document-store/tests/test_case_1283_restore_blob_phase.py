"""A restore's file-key check runs before anything is written, a dry run runs
it too, and the blob phase counts what it writes.

Provenance: CASE-1283 (review of its first fix).

- The key check ran after the plain restore had created its namespaces, so a
  refused key, or a store that could not answer for one (a raw error naming
  no key), left the namespaces created by a restore that wrote nothing else.
- A dry run never checked the keys, so it reported a restore as clear that
  the real run then refused; a merge dry run did not either.
- The blob phase announced every archived blob as uploaded, including the
  ones it leaves as they are because their key already holds the same bytes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from document_store.services.backup_engine import DirectRestoreEngine, RestoreEngineError
from document_store.services.file_storage_client import FileStorageError
from tests import test_case_1283_restore_blob_keys as blob_keys
from tests.test_case_1283_restore_blob_keys import (
    CONTENT,
    VICTIM,
    _file_archive,
    _files,
    _Store,
)
from tests.test_merge_restore import NAMESPACE, _seed_namespace

mongo = blob_keys.mongo  # the fixture


class _RefusingStore(_Store):
    """A store that refuses the HEAD (object lock, a credential that cannot
    read the key)."""

    async def exists(self, storage_key):
        raise FileStorageError("Failed to check file existence: 403 Forbidden")


async def _restore(mongo, storage, *, dry_run=False):
    events: list = []
    engine = DirectRestoreEngine(mongo, storage, events.append)
    engine._upsert_namespace = AsyncMock()
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=_file_archive()):
        await engine.run_restore(
            MagicMock(), NAMESPACE, allow_missing_identity=True, dry_run=dry_run,
        )
    return engine, events


@pytest.mark.asyncio
async def test_a_taken_key_refuses_before_any_namespace_is_created(mongo):
    storage = _Store({"F1": VICTIM})
    engine = DirectRestoreEngine(mongo, storage, lambda e: None)
    engine._upsert_namespace = AsyncMock()
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=_file_archive()), \
            pytest.raises(RestoreEngineError, match="never writes over an existing file's bytes"):
        await engine.run_restore(MagicMock(), NAMESPACE, allow_missing_identity=True)
    engine._upsert_namespace.assert_not_called()
    assert storage.puts == []


@pytest.mark.asyncio
async def test_a_store_that_cannot_answer_for_a_key_refuses_naming_it(mongo):
    storage = _RefusingStore()
    engine = DirectRestoreEngine(mongo, storage, lambda e: None)
    engine._upsert_namespace = AsyncMock()
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=_file_archive()), \
            pytest.raises(RestoreEngineError, match=r"file key F1 is free.*nothing was written"):
        await engine.run_restore(MagicMock(), NAMESPACE, allow_missing_identity=True)
    engine._upsert_namespace.assert_not_called()
    assert storage.puts == []
    assert await _files(mongo, NAMESPACE) == []


@pytest.mark.asyncio
async def test_the_blob_phase_counts_only_what_it_uploads(mongo):
    _, events = await _restore(mongo, _Store({"F1": CONTENT}))
    blobs = [e.message for e in events if e.phase == "phase_blobs"]
    assert blobs == [
        "Uploading 0 file blob(s); 1 key(s) already holding the same bytes are left as they are",
    ]


@pytest.mark.asyncio
async def test_a_dry_run_refuses_a_key_the_restore_would_refuse(mongo):
    storage = _Store({"F1": VICTIM})
    with pytest.raises(RestoreEngineError, match="never writes over an existing file's bytes"):
        await _restore(mongo, storage, dry_run=True)
    assert storage.puts == []


@pytest.mark.asyncio
async def test_a_dry_run_does_not_count_a_key_left_as_it_is(mongo):
    storage = _Store({"F1": CONTENT})
    _, events = await _restore(mongo, storage, dry_run=True)
    planned = [e.message for e in events if "would upload" in e.message]
    assert planned == [
        "dry run — would upload 0 file blob(s); 1 key(s) already holding the same "
        "bytes are left as they are",
    ]
    assert storage.puts == []


@pytest.mark.asyncio
async def test_a_merge_dry_run_refuses_a_key_the_merge_would_refuse(mongo):
    await _seed_namespace(mongo)
    storage = _Store({"F1": VICTIM})
    engine = DirectRestoreEngine(mongo, storage, lambda e: None)
    with patch("document_store.services.backup_engine.ArchiveReader", return_value=_file_archive()), \
            pytest.raises(RestoreEngineError, match="never writes over an existing file's bytes"):
        await engine.run_merge(MagicMock(), NAMESPACE, dry_run=True)
    assert storage.puts == []
