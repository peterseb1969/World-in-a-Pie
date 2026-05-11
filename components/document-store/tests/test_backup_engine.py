"""Unit tests for backup_engine.py (CASE-340).

backup_engine.py was added per CASE-266's redesign (direct Mongo cursor
reads instead of HTTP fan-out) and had 0% test coverage when CASE-334's
audit ran. These tests mock the Mongo cursor + ArchiveWriter/Reader
surfaces so they run in pure unit-test mode — no test-mongo container
needed, no infrastructure dependency.

Covers DirectBackupEngine + DirectRestoreEngine:

  - Pure functions (_build_query, _pct)
  - Pre-count gathering
  - Namespace config reading (present / missing)
  - run_backup happy path (empty namespace, basic flow, skip_documents)
  - Progress event emission
  - Empty-namespace restore precondition check
  - Namespace upsert (success / failure)
  - Batch insert (success / BulkWriteError)
  - run_restore happy path

The engine writes a real ZIP via wip_toolkit.archive.ArchiveWriter
under the hood; the tests stub the writer/reader to assert it's called
correctly without exercising ZIP I/O.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from pymongo.errors import BulkWriteError
from wip_toolkit.models import EntityCounts, Manifest, NamespaceConfig, ProgressEvent

from document_store.services.backup_engine import (
    BACKUP_ENTITY_ORDER,
    COLLECTION_MAP,
    DirectBackupEngine,
    DirectRestoreEngine,
    RestoreEngineError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncIter:
    """Minimal async-iterable wrapper around a sync iterable."""

    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


def _make_mongo_mock(*, docs_per_collection=None, counts_per_collection=None,
                     namespace_config_doc=None):
    """Build a mock AsyncIOMotorClient that supports client[db][coll].

    docs_per_collection: dict mapping coll_name → list of docs to yield via find().
    counts_per_collection: dict mapping coll_name → int returned by count_documents().
    namespace_config_doc: dict-or-None returned by namespaces.find_one().
    """
    docs_per_collection = docs_per_collection or {}
    counts_per_collection = counts_per_collection or {}

    collection_mocks: dict[str, MagicMock] = {}

    def _get_collection(coll_name):
        if coll_name in collection_mocks:
            return collection_mocks[coll_name]
        coll = MagicMock()
        # find() returns an async iterator over the configured docs
        coll.find = MagicMock(
            return_value=_AsyncIter(docs_per_collection.get(coll_name, []))
        )
        # count_documents is async, returns int
        coll.count_documents = AsyncMock(
            return_value=counts_per_collection.get(coll_name, 0)
        )
        # find_one is async; for namespaces, returns namespace_config_doc
        if coll_name == "namespaces":
            coll.find_one = AsyncMock(return_value=namespace_config_doc)
        else:
            coll.find_one = AsyncMock(return_value=None)
        # insert_many is async (success by default)
        coll.insert_many = AsyncMock()
        collection_mocks[coll_name] = coll
        return coll

    db_mock = MagicMock()
    db_mock.__getitem__ = lambda self, coll_name: _get_collection(coll_name)

    client_mock = MagicMock()
    client_mock.__getitem__ = lambda self, db_name: db_mock

    return client_mock, collection_mocks


def _collect_progress(events_list):
    """Build a progress callback that appends events to events_list."""
    def cb(event: ProgressEvent) -> None:
        events_list.append(event)
    return cb


# ---------------------------------------------------------------------------
# Module-level structure tests
# ---------------------------------------------------------------------------


class TestModuleStructure:
    """Verify the module-level constants hold the expected shape."""

    def test_backup_entity_order_includes_all_collections(self):
        # Every entity in BACKUP_ENTITY_ORDER must have an entry in COLLECTION_MAP
        for entity in BACKUP_ENTITY_ORDER:
            assert entity in COLLECTION_MAP

    def test_collection_map_shape(self):
        # Each entry is (db_name, coll_name) — both strings
        for entity, (db, coll) in COLLECTION_MAP.items():
            assert isinstance(db, str) and db
            assert isinstance(coll, str) and coll

    def test_backup_entity_order_covers_registry_entries(self):
        # registry_entries is the last entry (the audit doc surface)
        assert "registry_entries" in BACKUP_ENTITY_ORDER


# ---------------------------------------------------------------------------
# DirectBackupEngine — pure functions
# ---------------------------------------------------------------------------


class TestBuildQuery:
    """Verify the namespace + status query construction."""

    def test_default_excludes_deleted(self):
        engine = DirectBackupEngine(MagicMock(), None, lambda _: None)
        q = engine._build_query("kb", include_inactive=False)
        assert q == {"namespace": "kb", "status": {"$ne": "deleted"}}

    def test_include_inactive_drops_status_filter(self):
        engine = DirectBackupEngine(MagicMock(), None, lambda _: None)
        q = engine._build_query("kb", include_inactive=True)
        assert q == {"namespace": "kb"}


class TestPercent:
    """Verify DirectBackupEngine._pct math."""

    def test_zero_total_returns_zero(self):
        assert DirectBackupEngine._pct(0, 0) == 0.0
        assert DirectBackupEngine._pct(5, 0) == 0.0

    def test_partial_progress(self):
        # 5/10 → 45% (reserve 0-90% for entity reads)
        assert DirectBackupEngine._pct(5, 10) == 45.0

    def test_caps_at_90(self):
        # 100/10 would be 900% raw → capped at 90.0
        assert DirectBackupEngine._pct(100, 10) == 90.0


class TestRestorePercent:
    """Verify DirectRestoreEngine._pct math (different range)."""

    def test_zero_total_returns_ten(self):
        # Restore reserves 10% as base; zero-progress returns 10.0
        assert DirectRestoreEngine._pct(0, 0) == 10.0

    def test_partial_progress(self):
        # 5/10 → 10 + 50% of 80 = 50.0
        assert DirectRestoreEngine._pct(5, 10) == 50.0

    def test_caps_at_90(self):
        assert DirectRestoreEngine._pct(100, 10) == 90.0


# ---------------------------------------------------------------------------
# DirectBackupEngine — collaborator methods (async)
# ---------------------------------------------------------------------------


class TestPreCount:
    @pytest.mark.asyncio
    async def test_returns_count_per_entity_type(self):
        mongo, _ = _make_mongo_mock(
            counts_per_collection={
                "terminologies": 3,
                "terms": 42,
                "term_relations": 0,
                "templates": 5,
                "documents": 100,
                "files": 0,
                "registry_entries": 150,
            }
        )
        engine = DirectBackupEngine(mongo, None, lambda _: None)
        counts = await engine._pre_count("kb", include_inactive=False, skip_documents=False)
        assert counts["terminologies"] == 3
        assert counts["documents"] == 100
        assert counts["registry_entries"] == 150
        # All entity types accounted for
        for entity in BACKUP_ENTITY_ORDER:
            assert entity in counts

    @pytest.mark.asyncio
    async def test_skip_documents_zeros_doc_count_without_querying(self):
        mongo, colls = _make_mongo_mock(
            counts_per_collection={"documents": 100}
        )
        engine = DirectBackupEngine(mongo, None, lambda _: None)
        counts = await engine._pre_count("kb", include_inactive=False, skip_documents=True)
        # documents count returns 0; count_documents on documents collection was NOT called
        assert counts["documents"] == 0
        if "documents" in colls:
            colls["documents"].count_documents.assert_not_called()


class TestReadNamespaceConfig:
    @pytest.mark.asyncio
    async def test_returns_namespace_config_when_doc_present(self):
        mongo, _ = _make_mongo_mock(
            namespace_config_doc={
                "prefix": "kb",
                "description": "Knowledge base namespace",
                "isolation_mode": "strict",
                "id_config": {"terminologies": {"algorithm": "uuid7"}},
            }
        )
        engine = DirectBackupEngine(mongo, None, lambda _: None)
        config = await engine._read_namespace_config("kb")
        assert isinstance(config, NamespaceConfig)
        assert config.prefix == "kb"
        assert config.description == "Knowledge base namespace"
        assert config.isolation_mode == "strict"
        assert config.id_config == {"terminologies": {"algorithm": "uuid7"}}

    @pytest.mark.asyncio
    async def test_returns_none_when_namespace_missing(self):
        mongo, _ = _make_mongo_mock(namespace_config_doc=None)
        engine = DirectBackupEngine(mongo, None, lambda _: None)
        assert await engine._read_namespace_config("nonexistent") is None

    @pytest.mark.asyncio
    async def test_defaults_for_missing_fields(self):
        # ns doc with only prefix — other fields default
        mongo, _ = _make_mongo_mock(
            namespace_config_doc={"prefix": "minimal"}
        )
        engine = DirectBackupEngine(mongo, None, lambda _: None)
        config = await engine._read_namespace_config("minimal")
        assert config is not None
        assert config.prefix == "minimal"
        assert config.description == ""
        assert config.isolation_mode == "open"
        assert config.id_config is None


# ---------------------------------------------------------------------------
# DirectBackupEngine — run_backup pipeline
# ---------------------------------------------------------------------------


class TestRunBackupEmptyNamespace:
    """run_backup against a namespace with no entities still produces a valid manifest."""

    @pytest.mark.asyncio
    async def test_empty_namespace_emits_complete_event(self, tmp_path):
        mongo, _ = _make_mongo_mock(
            docs_per_collection={},
            counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER},
            namespace_config_doc={"prefix": "empty", "description": "test"},
        )
        events: list[ProgressEvent] = []
        engine = DirectBackupEngine(mongo, None, _collect_progress(events))

        archive_path = tmp_path / "empty-backup.zip"
        with patch(
            "document_store.services.backup_engine.ArchiveWriter"
        ) as mock_writer_cls:
            mock_writer = MagicMock()
            mock_writer.entity_count = MagicMock(return_value=0)
            mock_writer_cls.return_value = mock_writer

            await engine.run_backup("empty", archive_path)

        phases = [e.phase for e in events]
        assert "start" in phases
        assert "complete" in phases
        assert events[-1].phase == "complete"
        assert events[-1].percent == 100

        # Writer.write was called with a Manifest
        mock_writer.write.assert_called_once()
        manifest = mock_writer.write.call_args[0][0]
        assert isinstance(manifest, Manifest)
        assert manifest.namespace == "empty"
        assert manifest.counts.terminologies == 0
        assert manifest.counts.documents == 0


class TestRunBackupBasicFlow:
    """run_backup against a namespace with a few entities flows them through to writer."""

    @pytest.mark.asyncio
    async def test_writes_entities_via_add_entity(self, tmp_path):
        term_doc = {"_id": "internal-1", "terminology_id": "T1", "value": "GENDER", "namespace": "kb"}
        doc_doc = {"_id": "internal-2", "document_id": "D1", "namespace": "kb", "data": {"x": 1}}
        mongo, _ = _make_mongo_mock(
            docs_per_collection={
                "terminologies": [term_doc],
                "documents": [doc_doc],
            },
            counts_per_collection={
                "terminologies": 1,
                "documents": 1,
                "terms": 0,
                "term_relations": 0,
                "templates": 0,
                "files": 0,
                "registry_entries": 0,
            },
            namespace_config_doc={"prefix": "kb", "description": "kb test"},
        )
        events: list[ProgressEvent] = []
        engine = DirectBackupEngine(mongo, None, _collect_progress(events))

        with patch(
            "document_store.services.backup_engine.ArchiveWriter"
        ) as mock_writer_cls:
            mock_writer = MagicMock()
            mock_writer.entity_count = MagicMock(return_value=1)
            mock_writer_cls.return_value = mock_writer
            await engine.run_backup("kb", tmp_path / "kb-backup.zip")

        # add_entity called twice — once per fed entity
        add_calls = mock_writer.add_entity.call_args_list
        assert len(add_calls) == 2
        entity_types_seen = {call[0][0] for call in add_calls}
        assert entity_types_seen == {"terminologies", "documents"}

        # _id was stripped before adding
        for call in add_calls:
            payload = call[0][1]
            assert "_id" not in payload

    @pytest.mark.asyncio
    async def test_skip_documents_omits_documents_phase(self, tmp_path):
        mongo, _ = _make_mongo_mock(
            docs_per_collection={
                "documents": [{"document_id": "D1", "namespace": "kb"}],
            },
            counts_per_collection={
                "terminologies": 0, "terms": 0, "term_relations": 0,
                "templates": 0, "documents": 1, "files": 0, "registry_entries": 0,
            },
            namespace_config_doc={"prefix": "kb"},
        )
        events: list[ProgressEvent] = []
        engine = DirectBackupEngine(mongo, None, _collect_progress(events))

        with patch(
            "document_store.services.backup_engine.ArchiveWriter"
        ) as mock_writer_cls:
            mock_writer = MagicMock()
            mock_writer.entity_count = MagicMock(return_value=0)
            mock_writer_cls.return_value = mock_writer
            await engine.run_backup("kb", tmp_path / "skip.zip", skip_documents=True)

        # No "phase_documents" event emitted
        phases = [e.phase for e in events]
        assert "phase_documents" not in phases
        # add_entity NEVER called with "documents"
        for call in mock_writer.add_entity.call_args_list:
            assert call[0][0] != "documents"


# ---------------------------------------------------------------------------
# DirectRestoreEngine — collaborator methods
# ---------------------------------------------------------------------------


class TestCheckNamespaceEmpty:
    @pytest.mark.asyncio
    async def test_passes_when_all_collections_empty(self):
        mongo, _ = _make_mongo_mock(
            counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER}
        )
        engine = DirectRestoreEngine(mongo, None, lambda _: None)
        # Should not raise
        await engine._check_namespace_empty("new-ns")

    @pytest.mark.asyncio
    async def test_raises_when_a_collection_has_data(self):
        # Spread one entity across the collection map — pick "documents"
        counts = {e: 0 for e in BACKUP_ENTITY_ORDER}
        counts["documents"] = 1
        mongo, _ = _make_mongo_mock(counts_per_collection=counts)
        engine = DirectRestoreEngine(mongo, None, lambda _: None)
        with pytest.raises(RestoreEngineError, match="not empty"):
            await engine._check_namespace_empty("existing-ns")


class TestUpsertNamespace:
    @pytest.mark.asyncio
    async def test_calls_registry_put_with_namespace_config_from_manifest(self):
        mongo, _ = _make_mongo_mock()
        engine = DirectRestoreEngine(
            mongo,
            None,
            lambda _: None,
            registry_base_url="http://registry:8001",
            registry_api_key="test-key",
        )
        manifest = Manifest(
            format_version="2.0",
            namespace="kb",
            namespace_config=NamespaceConfig(
                prefix="kb",
                description="Knowledge base",
                isolation_mode="strict",
                id_config={"terminologies": {"algorithm": "uuid7"}},
            ),
            counts=EntityCounts(),
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"

        with patch(
            "httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.put = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await engine._upsert_namespace("kb", manifest)

            mock_client.put.assert_awaited_once()
            call_kwargs = mock_client.put.await_args.kwargs
            assert "headers" in call_kwargs
            assert call_kwargs["headers"]["X-API-Key"] == "test-key"
            body = call_kwargs["json"]
            assert body["description"] == "Knowledge base"
            assert body["isolation_mode"] == "strict"
            assert body["id_config"] == {"terminologies": {"algorithm": "uuid7"}}

    @pytest.mark.asyncio
    async def test_raises_on_non_2xx_response(self):
        mongo, _ = _make_mongo_mock()
        engine = DirectRestoreEngine(mongo, None, lambda _: None)
        manifest = Manifest(
            format_version="2.0",
            namespace="kb",
            namespace_config=NamespaceConfig(prefix="kb"),
            counts=EntityCounts(),
        )

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "internal server error"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.put = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RestoreEngineError, match="Failed to upsert"):
                await engine._upsert_namespace("kb", manifest)


class TestInsertBatch:
    @pytest.mark.asyncio
    async def test_calls_insert_many_with_ordered_false(self):
        mongo, _ = _make_mongo_mock()
        engine = DirectRestoreEngine(mongo, None, lambda _: None)
        coll = MagicMock()
        coll.insert_many = AsyncMock()

        batch = [{"a": 1}, {"a": 2}]
        await engine._insert_batch(coll, batch, "documents")

        coll.insert_many.assert_awaited_once_with(batch, ordered=False)

    @pytest.mark.asyncio
    async def test_raises_restore_engine_error_on_bulk_write_error(self):
        mongo, _ = _make_mongo_mock()
        engine = DirectRestoreEngine(mongo, None, lambda _: None)
        coll = MagicMock()
        bwe = BulkWriteError({"writeErrors": [{"errmsg": "duplicate key"}]})
        coll.insert_many = AsyncMock(side_effect=bwe)

        with pytest.raises(RestoreEngineError, match="Bulk insert failed for documents"):
            await engine._insert_batch(coll, [{"a": 1}], "documents")


# ---------------------------------------------------------------------------
# DirectRestoreEngine — run_restore pipeline
# ---------------------------------------------------------------------------


class TestRunRestoreBasicFlow:
    """run_restore reads manifest + entities from archive, calls collection inserts."""

    @pytest.mark.asyncio
    async def test_restore_into_empty_namespace_succeeds(self, tmp_path):
        # Mock mongo: all counts zero (target namespace empty for precondition);
        # insert_many succeeds (default AsyncMock)
        mongo, _ = _make_mongo_mock(
            counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER}
        )
        events: list[ProgressEvent] = []
        engine = DirectRestoreEngine(mongo, None, _collect_progress(events))

        # Build manifest the reader will return
        manifest = Manifest(
            format_version="2.0",
            namespace="kb",
            namespace_config=NamespaceConfig(prefix="kb", isolation_mode="open"),
            counts=EntityCounts(terminologies=2, documents=3),
        )

        # ArchiveReader is used as a context manager
        mock_reader = MagicMock()
        mock_reader.read_manifest = MagicMock(return_value=manifest)
        mock_reader.read_entities = MagicMock(side_effect=lambda et: {
            "terminologies": [{"terminology_id": "T1"}, {"terminology_id": "T2"}],
            "terms": [],
            "term_relations": [],
            "templates": [],
            "documents": [{"document_id": "D1"}, {"document_id": "D2"}, {"document_id": "D3"}],
            "files": [],
            "registry_entries": [],
        }[et])
        mock_reader.__enter__ = MagicMock(return_value=mock_reader)
        mock_reader.__exit__ = MagicMock(return_value=None)

        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=mock_reader,
        ), patch("httpx.AsyncClient") as mock_httpx_cls:
            # Stub the namespace-upsert httpx.put
            ok_resp = MagicMock(status_code=200, text="ok")
            mock_httpx = MagicMock()
            mock_httpx.put = AsyncMock(return_value=ok_resp)
            mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
            mock_httpx.__aexit__ = AsyncMock(return_value=None)
            mock_httpx_cls.return_value = mock_httpx

            await engine.run_restore(tmp_path / "kb.zip", "kb")

        phases = [e.phase for e in events]
        assert "start" in phases
        assert "phase_validate" in phases
        assert "phase_namespace" in phases
        assert events[-1].phase == "complete"
        assert events[-1].percent == 100

    @pytest.mark.asyncio
    async def test_restore_refuses_non_empty_namespace(self, tmp_path):
        # Target namespace has data → precondition fails
        counts = {e: 0 for e in BACKUP_ENTITY_ORDER}
        counts["documents"] = 1
        mongo, _ = _make_mongo_mock(counts_per_collection=counts)
        events: list[ProgressEvent] = []
        engine = DirectRestoreEngine(mongo, None, _collect_progress(events))

        manifest = Manifest(
            format_version="2.0",
            namespace="kb",
            namespace_config=NamespaceConfig(prefix="kb"),
            counts=EntityCounts(),
        )
        mock_reader = MagicMock()
        mock_reader.read_manifest = MagicMock(return_value=manifest)
        mock_reader.__enter__ = MagicMock(return_value=mock_reader)
        mock_reader.__exit__ = MagicMock(return_value=None)

        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=mock_reader,
        ):
            with pytest.raises(RestoreEngineError, match="not empty"):
                await engine.run_restore(tmp_path / "kb.zip", "kb")
