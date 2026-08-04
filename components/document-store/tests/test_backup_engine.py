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

import asyncio
import contextlib
import os
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import BulkWriteError
from wip_archive.models import EntityCounts, Manifest, NamespaceConfig, ProgressEvent

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
        # find() returns a cursor mock whose .sort() yields the configured
        # docs — mirroring the engine's find(...).sort(natural_key) chain;
        # the sort spec lands in cursor_mock.sort.call_args for assertions
        cursor = MagicMock()
        cursor.sort = MagicMock(
            return_value=_AsyncIter(docs_per_collection.get(coll_name, []))
        )
        coll.find = MagicMock(return_value=cursor)
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
        for _entity, (db, coll) in COLLECTION_MAP.items():
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

    def test_query_is_namespace_alone(self):
        # A backup is a full copy: every entity, every status. No status
        # filter may reappear here — an archive missing inactive or archived
        # entities that live data references would be a restore trap.
        engine = DirectBackupEngine(MagicMock(), None, lambda _: None)
        q = engine._build_query("kb")
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
        counts = await engine._pre_count("kb", skip_documents=False)
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
        counts = await engine._pre_count("kb", skip_documents=True)
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
    async def test_captures_allowed_external_refs_and_deletion_mode(self):
        """The full namespace config round-trips — a restored namespace must
        not silently revert its allowlist or deletion policy to defaults."""
        mongo, _ = _make_mongo_mock(
            namespace_config_doc={
                "prefix": "library",
                "isolation_mode": "open",
                "allowed_external_refs": ["kb"],
                "deletion_mode": "full",
            }
        )
        engine = DirectBackupEngine(mongo, None, lambda _: None)
        config = await engine._read_namespace_config("library")
        assert config.allowed_external_refs == ["kb"]
        assert config.deletion_mode == "full"

    @pytest.mark.asyncio
    async def test_absent_config_fields_stay_none(self):
        """None (not [] / 'retain') marks fields the source doc did not carry,
        so the restore upsert knows to omit rather than reset them."""
        mongo, _ = _make_mongo_mock(namespace_config_doc={"prefix": "minimal"})
        engine = DirectBackupEngine(mongo, None, lambda _: None)
        config = await engine._read_namespace_config("minimal")
        assert config.allowed_external_refs is None
        assert config.deletion_mode is None

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


class TestRunBackupMultiNamespace:
    """run_backup over a list of namespaces (CASE-542) builds a v3 manifest."""

    @pytest.mark.asyncio
    async def test_two_namespaces_manifest(self, tmp_path):
        mongo, _ = _make_mongo_mock(
            docs_per_collection={},
            counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER},
            namespace_config_doc={"prefix": "x", "description": "test"},
        )
        events: list[ProgressEvent] = []
        engine = DirectBackupEngine(mongo, None, _collect_progress(events))

        with patch(
            "document_store.services.backup_engine.ArchiveWriter"
        ) as mock_writer_cls:
            mock_writer = MagicMock()
            mock_writer.entity_count = MagicMock(return_value=0)
            mock_writer_cls.return_value = mock_writer

            await engine.run_backup(["alpha", "beta"], tmp_path / "multi.zip")

        manifest = mock_writer.write.call_args[0][0]
        # The engine writes 3.1 — canonical endpoint declarations, no stored
        # source_ref/target_ref constraint. Restore of 3.0 archives is still
        # exercised elsewhere in this file, which is why those fixtures stay.
        assert manifest.format_version == "3.1"
        assert manifest.namespace_prefixes() == ["alpha", "beta"]
        assert manifest.namespace == ""  # not single → no convenience field
        # add_entity for the (empty) namespaces would carry the namespace kwarg
        # if there were rows; here we just assert the per-namespace manifest shape.
        assert {e.prefix for e in manifest.namespaces} == {"alpha", "beta"}
        assert events[-1].phase == "complete"


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
    async def test_export_sorts_every_entity_stream_by_natural_key(self, tmp_path):
        """Every entity stream is exported through a natural-key sort.

        Unordered exports made archive bytes depend on the Mongo query plan:
        identical corpora produced row-permuted (and up to ~32% worse
        compressed) archives. The cursor must be built with allow_disk_use
        (the sort may not ride an index on big namespaces) and sorted with
        the entity's EXPORT_SORT_ORDER spec.
        """
        from document_store.services.backup_engine import EXPORT_SORT_ORDER

        mongo, collections = _make_mongo_mock(
            docs_per_collection={},
            counts_per_collection={et: 0 for et in BACKUP_ENTITY_ORDER},
            namespace_config_doc={"prefix": "kb", "description": "kb test"},
        )
        engine = DirectBackupEngine(mongo, None, _collect_progress([]))

        with patch(
            "document_store.services.backup_engine.ArchiveWriter"
        ) as mock_writer_cls:
            mock_writer = MagicMock()
            mock_writer.entity_count = MagicMock(return_value=0)
            mock_writer_cls.return_value = mock_writer
            await engine.run_backup("kb", tmp_path / "sorted.zip")

        for entity_type in BACKUP_ENTITY_ORDER:
            _, coll_name = COLLECTION_MAP[entity_type]
            coll = collections[coll_name]
            find_kwargs = coll.find.call_args.kwargs
            assert find_kwargs.get("allow_disk_use") is True, entity_type
            sort_spec = coll.find.return_value.sort.call_args.args[0]
            assert sort_spec == EXPORT_SORT_ORDER[entity_type], entity_type

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
            format_version="3.0",
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

            await engine._upsert_namespace("kb", manifest.namespace_config)

            mock_client.put.assert_awaited_once()
            call_kwargs = mock_client.put.await_args.kwargs
            assert "headers" in call_kwargs
            assert call_kwargs["headers"]["X-API-Key"] == "test-key"
            body = call_kwargs["json"]
            assert body["description"] == "Knowledge base"
            assert body["isolation_mode"] == "strict"
            assert body["id_config"] == {"terminologies": {"algorithm": "uuid7"}}

    @pytest.mark.asyncio
    async def test_body_includes_allowlist_and_deletion_mode_when_present(self):
        """Explicit values round-trip — including an EMPTY allowlist, which is
        a real config, distinct from an archive that predates the field."""
        mongo, _ = _make_mongo_mock()
        engine = DirectRestoreEngine(
            mongo, None, lambda _: None,
            registry_base_url="http://registry:8001",
            registry_api_key="test-key",
        )
        config = NamespaceConfig(
            prefix="library",
            allowed_external_refs=[],
            deletion_mode="retain",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.put = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await engine._upsert_namespace("library", config)

            body = mock_client.put.await_args.kwargs["json"]
            assert body["allowed_external_refs"] == []
            assert body["deletion_mode"] == "retain"

    @pytest.mark.asyncio
    async def test_fresh_restore_drops_the_source_id_config(self):
        """A fresh restore re-mints every id, so it must NOT stamp the target
        with the source's id_config. preserve_id_config=False keeps a prefixed
        source scheme out of the PUT body, so the target defaults to UUID7
        instead of re-minting the source's exact prefixed ids and colliding
        with the live original on the global entry_id index (CASE-784). Every
        other config field still carries over."""
        mongo, _ = _make_mongo_mock()
        engine = DirectRestoreEngine(
            mongo, None, lambda _: None,
            registry_base_url="http://registry:8001",
            registry_api_key="test-key",
        )
        config = NamespaceConfig(
            prefix="src",
            description="prefixed source",
            isolation_mode="open",
            id_config={
                "documents": {"algorithm": "prefixed", "prefix": "SRC-", "pad": 6}
            },
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.put = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await engine._upsert_namespace(
                "copy", config, preserve_id_config=False
            )

            body = mock_client.put.await_args.kwargs["json"]
            assert "id_config" not in body  # the fix: source scheme not inherited
            assert body["description"] == "prefixed source"  # rest still carries
            assert body["isolation_mode"] == "open"

    @pytest.mark.asyncio
    async def test_body_omits_fields_absent_from_old_archives(self):
        """A pre-fix archive (fields None) must not touch an existing
        namespace's allowlist or deletion policy."""
        mongo, _ = _make_mongo_mock()
        engine = DirectRestoreEngine(
            mongo, None, lambda _: None,
            registry_base_url="http://registry:8001",
            registry_api_key="test-key",
        )
        config = NamespaceConfig(prefix="kb", description="old archive")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "ok"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.put = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await engine._upsert_namespace("kb", config)

            body = mock_client.put.await_args.kwargs["json"]
            assert "allowed_external_refs" not in body
            assert "deletion_mode" not in body

    @pytest.mark.asyncio
    async def test_retain_to_full_guard_warns_and_continues(self):
        """Restoring deletion_mode='full' over an existing retain namespace
        must not abort the restore and must not bypass the confirmation
        guard — the field is skipped with a loud warning and the rest of the
        config is applied."""
        mongo, _ = _make_mongo_mock()
        events = []
        engine = DirectRestoreEngine(
            mongo, None, events.append,
            registry_base_url="http://registry:8001",
            registry_api_key="test-key",
        )
        config = NamespaceConfig(
            prefix="lab",
            allowed_external_refs=["wip"],
            deletion_mode="full",
        )

        guard_response = MagicMock()
        guard_response.status_code = 400
        guard_response.text = (
            "Set confirm_enable_deletion=true in the body to flip "
            "deletion_mode from 'retain' to 'full'"
        )
        ok_response = MagicMock()
        ok_response.status_code = 200
        ok_response.text = "ok"

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.put = AsyncMock(side_effect=[guard_response, ok_response])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await engine._upsert_namespace("lab", config)

            assert mock_client.put.await_count == 2
            retry_body = mock_client.put.await_args_list[1].kwargs["json"]
            assert "deletion_mode" not in retry_body
            assert retry_body["allowed_external_refs"] == ["wip"]
            warnings = [e for e in events if "NOT applied" in e.message]
            assert len(warnings) == 1
            assert "lab" in warnings[0].message

    @pytest.mark.asyncio
    async def test_raises_on_non_2xx_response(self):
        mongo, _ = _make_mongo_mock()
        engine = DirectRestoreEngine(mongo, None, lambda _: None)
        manifest = Manifest(
            format_version="3.0",
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
                await engine._upsert_namespace("kb", manifest.namespace_config)


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
            format_version="3.0",
            namespace="kb",
            namespace_config=NamespaceConfig(prefix="kb", isolation_mode="open"),
            counts=EntityCounts(terminologies=2, documents=3),
        )

        # ArchiveReader is used as a context manager
        mock_reader = MagicMock()
        mock_reader.read_manifest = MagicMock(return_value=manifest)
        mock_reader.read_manifest_raw = MagicMock(return_value={})
        mock_reader.entity_count = MagicMock(return_value=0)
        mock_reader.read_entities = MagicMock(side_effect=lambda et, namespace=None: {
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
            format_version="3.0",
            namespace="kb",
            namespace_config=NamespaceConfig(prefix="kb"),
            counts=EntityCounts(),
        )
        mock_reader = MagicMock()
        mock_reader.read_manifest = MagicMock(return_value=manifest)
        mock_reader.read_manifest_raw = MagicMock(return_value={})
        mock_reader.entity_count = MagicMock(return_value=0)
        mock_reader.__enter__ = MagicMock(return_value=mock_reader)
        mock_reader.__exit__ = MagicMock(return_value=None)

        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=mock_reader,
        ), pytest.raises(RestoreEngineError, match="not empty"):
            await engine.run_restore(tmp_path / "kb.zip", "kb")


class TestRestoreRedirectGuard:
    """An ID-preserving restore must never write under a different namespace
    name: the archived entities and registry entries embed the source
    namespace, so a redirected insert would empty-check one namespace and
    write records carrying another. The engine rejects the redirect before
    any precondition or write."""

    def _reader_for(self, namespace: str):
        manifest = Manifest(
            format_version="3.0",
            namespace=namespace,
            namespace_config=NamespaceConfig(prefix=namespace),
            counts=EntityCounts(),
        )
        mock_reader = MagicMock()
        mock_reader.read_manifest = MagicMock(return_value=manifest)
        mock_reader.read_manifest_raw = MagicMock(return_value={})
        mock_reader.entity_count = MagicMock(return_value=0)
        mock_reader.list_namespaces = MagicMock(return_value=[namespace])
        mock_reader.__enter__ = MagicMock(return_value=mock_reader)
        mock_reader.__exit__ = MagicMock(return_value=None)
        return mock_reader

    @pytest.mark.asyncio
    async def test_differing_target_namespace_rejected(self, tmp_path):
        mongo, _ = _make_mongo_mock(
            counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER}
        )
        engine = DirectRestoreEngine(mongo, None, _collect_progress([]))

        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=self._reader_for("prod"),
        ), pytest.raises(RestoreEngineError, match="cannot re-namespace"):
            await engine.run_restore(tmp_path / "prod.zip", "prod-bak")

    @pytest.mark.asyncio
    async def test_matching_target_namespace_accepted(self, tmp_path):
        mongo, _ = _make_mongo_mock(
            counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER}
        )
        events = []
        engine = DirectRestoreEngine(mongo, None, _collect_progress(events))
        reader = self._reader_for("prod")
        reader.read_entities = MagicMock(return_value=[])

        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=reader,
        ), patch("httpx.AsyncClient") as mock_httpx_cls:
            ok_resp = MagicMock(status_code=200, text="ok")
            mock_httpx = MagicMock()
            mock_httpx.put = AsyncMock(return_value=ok_resp)
            mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
            mock_httpx.__aexit__ = AsyncMock(return_value=None)
            mock_httpx_cls.return_value = mock_httpx

            await engine.run_restore(tmp_path / "prod.zip", "prod")

        assert events[-1].phase == "complete"


class TestRunRestoreDryRun:
    """dry_run runs every precondition, reports would-restore counts, and
    writes nothing: no namespace upsert, no inserts, no blobs."""

    def _reader(self):
        manifest = Manifest(
            format_version="3.0",
            namespace="kb",
            namespace_config=NamespaceConfig(prefix="kb", isolation_mode="open"),
            counts=EntityCounts(terminologies=2, documents=3),
        )
        mock_reader = MagicMock()
        mock_reader.read_manifest = MagicMock(return_value=manifest)
        mock_reader.read_manifest_raw = MagicMock(return_value={})
        mock_reader.list_namespaces = MagicMock(return_value=["kb"])
        mock_reader.list_blobs = MagicMock(return_value=[])
        # The manifest above has no per-namespace entries, so the dry-run
        # report falls back to counting archive lines — stub that count.
        # registry_entries must be non-zero: the identity precondition
        # (entities without identity rows refuse) runs in dry-run too.
        mock_reader.entity_count = MagicMock(
            side_effect=lambda et, namespace="": {
                "terminologies": 2, "documents": 3, "registry_entries": 2,
            }.get(et, 0)
        )
        mock_reader.__enter__ = MagicMock(return_value=mock_reader)
        mock_reader.__exit__ = MagicMock(return_value=None)
        return mock_reader

    @pytest.mark.asyncio
    async def test_dry_run_writes_nothing_and_reports_counts(self, tmp_path):
        mongo, collections = _make_mongo_mock(
            counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER}
        )
        events = []
        engine = DirectRestoreEngine(mongo, None, _collect_progress(events))
        reader = self._reader()

        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=reader,
        ), patch("httpx.AsyncClient") as mock_httpx_cls:
            await engine.run_restore(tmp_path / "kb.zip", "kb", dry_run=True)

        # No namespace upsert (httpx never even instantiated), no entity reads,
        # no inserts on any collection.
        assert not mock_httpx_cls.called
        assert not reader.read_entities.called
        for coll in collections.values():
            assert not coll.insert_many.called

        # Preconditions ran, the report names the manifest counts, and the
        # job completed as a dry run.
        phases = [e.phase for e in events]
        assert "phase_validate" in phases
        report = next(e for e in events if e.phase == "phase_dry_run")
        assert "terminologies=2" in report.message
        assert "documents=3" in report.message
        assert events[-1].phase == "complete"
        assert "Dry run complete" in events[-1].message

    @pytest.mark.asyncio
    async def test_dry_run_still_fails_on_non_empty_target(self, tmp_path):
        counts = {e: 0 for e in BACKUP_ENTITY_ORDER}
        counts["documents"] = 1
        mongo, _ = _make_mongo_mock(counts_per_collection=counts)
        engine = DirectRestoreEngine(mongo, None, _collect_progress([]))

        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=self._reader(),
        ), pytest.raises(RestoreEngineError, match="not empty"):
            await engine.run_restore(tmp_path / "kb.zip", "kb", dry_run=True)

    @pytest.mark.asyncio
    async def test_dry_run_respects_skip_flags_in_report(self, tmp_path):
        mongo, _ = _make_mongo_mock(
            counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER}
        )
        events = []
        engine = DirectRestoreEngine(mongo, None, _collect_progress(events))

        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=self._reader(),
        ):
            await engine.run_restore(
                tmp_path / "kb.zip", "kb",
                dry_run=True, skip_documents=True, skip_files=True,
            )

        report = next(e for e in events if e.phase == "phase_dry_run")
        assert "documents=skipped" in report.message
        assert "files=skipped" in report.message


# ---------------------------------------------------------------------------
# Composite-key claim rebuild
# ---------------------------------------------------------------------------


class TestClaimDerivation:
    """Registry entries imply claim rows; claims are the uniqueness gate.

    Derived inline as entries are written — each entry is already in hand at
    that point, so a namespace-wide re-scan afterwards is pure waste on top of
    the inserts that are genuinely required.
    """

    NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

    def test_primary_key_and_every_synonym_are_claimed(self):
        rows = DirectRestoreEngine._claim_rows_for([{
            "entry_id": "E1",
            "namespace": "kb",
            "entity_type": "templates",
            "primary_composite_key_hash": "hash-primary",
            "synonyms": [{
                "namespace": "kb",
                "entity_type": "templates",
                "composite_key_hash": "hash-syn",
            }],
        }], self.NOW)

        assert [(r["composite_key_hash"], r["kind"]) for r in rows] == [
            ("hash-primary", "primary"),
            ("hash-syn", "synonym"),
        ]
        assert {r["owner_entry_id"] for r in rows} == {"E1"}
        assert {r["state"] for r in rows} == {"confirmed"}

    def test_synonym_claim_uses_the_synonyms_own_scope(self):
        # A synonym may live in a different namespace/entity_type than the
        # entry that owns it; the claim key must follow the synonym, or the
        # gate protects the wrong (namespace, type) pair.
        rows = DirectRestoreEngine._claim_rows_for([{
            "entry_id": "E1",
            "namespace": "kb",
            "entity_type": "documents",
            "primary_composite_key_hash": "hash-primary",
            "synonyms": [{
                "namespace": "legacy",
                "entity_type": "terms",
                "composite_key_hash": "hash-syn",
            }],
        }], self.NOW)

        synonym_claim = next(r for r in rows if r["kind"] == "synonym")
        assert synonym_claim["namespace"] == "legacy"
        assert synonym_claim["entity_type"] == "terms"

    def test_empty_hashes_are_not_claimed(self):
        # An empty hash means "this entity opts out of dedup" (legacy template
        # entries, identity-less documents). The claims unique index exempts
        # it; so must this, or every such entry collides with the next.
        rows = DirectRestoreEngine._claim_rows_for([{
            "entry_id": "E1",
            "namespace": "kb",
            "entity_type": "templates",
            "primary_composite_key_hash": "",
            "synonyms": [{
                "namespace": "kb",
                "entity_type": "templates",
                "composite_key_hash": "",
            }],
        }], self.NOW)

        assert rows == []


class TestClaimInsertion:
    @staticmethod
    def _engine(*, insert_error=None):
        mongo, _colls = _make_mongo_mock()
        claims = mongo[os.environ.get("REGISTRY_DATABASE_NAME", "wip_registry")]["composite_key_claims"]
        if insert_error is not None:
            claims.insert_many = AsyncMock(side_effect=insert_error)
        events: list[ProgressEvent] = []
        engine = DirectRestoreEngine(mongo, None, _collect_progress(events))
        return engine, claims, events

    @staticmethod
    def _rows(owner="E1"):
        return [{
            "namespace": "kb", "entity_type": "templates",
            "composite_key_hash": "hash-taken", "owner_entry_id": owner,
            "kind": "primary", "state": "confirmed",
        }]

    @staticmethod
    def _duplicate_error(owner="E1"):
        return BulkWriteError({
            "writeErrors": [{
                "code": 11000,
                "errmsg": "duplicate key",
                "index": 0,
                "op": {
                    "namespace": "kb",
                    "entity_type": "templates",
                    "composite_key_hash": "hash-taken",
                    "owner_entry_id": owner,
                },
            }],
        })

    @pytest.mark.asyncio
    async def test_claims_are_inserted_unordered(self):
        engine, claims, _ = self._engine()

        await engine._insert_claims("kb", self._rows())

        assert claims.insert_many.call_args.kwargs["ordered"] is False

    @pytest.mark.asyncio
    async def test_a_key_held_by_another_entry_is_counted_as_theirs(self):
        engine, claims, _ = self._engine(insert_error=self._duplicate_error())
        claims.find_one = AsyncMock(return_value={"owner_entry_id": "SOMEONE-ELSE"})

        claimed, taken_by_other = await engine._insert_claims("kb", self._rows())

        assert (claimed, taken_by_other) == (0, 1)

    @pytest.mark.asyncio
    async def test_a_key_this_entry_already_holds_is_not_a_collision(self):
        # Re-claiming what this entry already owns is the write being
        # idempotent, not a collision — warning here would cry wolf on every
        # merge into a namespace that already has claims.
        engine, claims, _ = self._engine(insert_error=self._duplicate_error())
        claims.find_one = AsyncMock(return_value={"owner_entry_id": "E1"})

        claimed, taken_by_other = await engine._insert_claims("kb", self._rows())

        assert (claimed, taken_by_other) == (0, 0)

    @pytest.mark.asyncio
    async def test_non_duplicate_write_error_is_fatal(self):
        error = BulkWriteError({
            "writeErrors": [{"code": 121, "errmsg": "document validation failed"}],
        })
        engine, _claims, _events = self._engine(insert_error=error)

        with pytest.raises(RestoreEngineError, match="Claim rebuild failed"):
            await engine._insert_claims("kb", self._rows())

    @pytest.mark.asyncio
    async def test_nothing_to_claim_touches_nothing(self):
        engine, claims, _ = self._engine()

        assert await engine._insert_claims("kb", []) == (0, 0)
        assert not claims.insert_many.called

    def test_only_a_foreign_owner_warns(self):
        engine, _claims, events = self._engine()

        engine._report_claims("kb", claimed=5, taken_by_other=0)
        assert [e for e in events if e.phase == "warning"] == []

        engine._report_claims("kb", claimed=0, taken_by_other=2)
        warning = next(e for e in events if e.phase == "warning")
        assert "already" in warning.message and "claimed" in warning.message


# ---------------------------------------------------------------------------
# CASE-801 — the archive finalize must not run on the event loop
# ---------------------------------------------------------------------------


class TestFinalizeDoesNotBlockTheEventLoop:
    """ArchiveWriter.write() compresses the whole archive in one synchronous
    call whose duration scales with total bytes. Run on the event loop it
    starves every other task in the process — including the health endpoint,
    which is how an instance-wide backup took the service out of its ingress
    for ~2.5 minutes while a 853 MB archive was assembled.

    The guard is behavioural rather than a check that to_thread was called:
    what matters is that other coroutines still get scheduled while the
    archive is being written, however that ends up being arranged.
    """

    @pytest.mark.asyncio
    async def test_other_coroutines_still_run_while_the_archive_is_written(self, tmp_path):
        mongo, _ = _make_mongo_mock(
            docs_per_collection={},
            counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER},
            namespace_config_doc={"prefix": "empty", "description": "test"},
        )
        engine = DirectBackupEngine(mongo, None, lambda _: None)

        # Stands in for the DEFLATE pass: synchronous, and long enough that a
        # loop-blocking implementation cannot hide behind scheduler noise.
        block_s = 0.4
        write_window: list[float] = []

        def blocking_write(_manifest):
            write_window.append(time.monotonic())
            time.sleep(block_s)
            write_window.append(time.monotonic())
            return tmp_path / "empty-backup.zip"

        # Stands in for /health being served while the backup finalizes.
        ticks: list[float] = []

        async def heartbeat():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)

        with patch(
            "document_store.services.backup_engine.ArchiveWriter"
        ) as mock_writer_cls:
            mock_writer = MagicMock()
            mock_writer.entity_count = MagicMock(return_value=0)
            mock_writer.write = blocking_write
            mock_writer_cls.return_value = mock_writer

            beat = asyncio.create_task(heartbeat())
            await asyncio.sleep(0)  # let the heartbeat reach its first await
            try:
                await engine.run_backup("empty", tmp_path / "empty-backup.zip")
            finally:
                beat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await beat

        assert len(write_window) == 2, "the stubbed writer did not run"
        started, ended = write_window
        during = [t for t in ticks if started <= t <= ended]
        # With write() on the loop this is 0: nothing else can be scheduled
        # for the whole compression. The bound is deliberately loose — the
        # claim is "the loop kept turning", not a throughput figure.
        assert len(during) >= 3, (
            f"the event loop was starved while the archive was written: "
            f"{len(during)} heartbeat tick(s) in {ended - started:.2f}s"
        )


class TestRestoreIdentityPrecondition:
    """An archive with entity rows but no registry identity rows restores
    into a namespace where nothing resolves by id — every list surface looks
    healthy while point reads fail, and post-restore integrity validation
    cannot see it (it checks MongoDB against itself; identity lives in the
    Registry). The engine refuses up front unless explicitly overridden."""

    def _reader(self, *, registry_rows: int, raw_manifest: dict | None = None):
        manifest = Manifest(
            format_version="3.0",
            namespace="kb",
            namespace_config=NamespaceConfig(prefix="kb", isolation_mode="open"),
            counts=EntityCounts(terminologies=2),
        )
        mock_reader = MagicMock()
        mock_reader.read_manifest = MagicMock(return_value=manifest)
        mock_reader.read_manifest_raw = MagicMock(
            return_value=raw_manifest or {}
        )
        mock_reader.entity_count = MagicMock(
            side_effect=lambda et, namespace=None: {
                "terminologies": 2,
                "registry_entries": registry_rows,
            }.get(et, 0)
        )
        mock_reader.read_entities = MagicMock(side_effect=lambda et, namespace=None: {
            "terminologies": [{"terminology_id": "T1"}, {"terminology_id": "T2"}],
        }.get(et, []))
        mock_reader.__enter__ = MagicMock(return_value=mock_reader)
        mock_reader.__exit__ = MagicMock(return_value=None)
        return mock_reader

    def _httpx_ok(self):
        ok_resp = MagicMock(status_code=200, text="ok")
        mock_httpx = MagicMock()
        mock_httpx.put = AsyncMock(return_value=ok_resp)
        mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
        mock_httpx.__aexit__ = AsyncMock(return_value=None)
        return mock_httpx

    @pytest.mark.asyncio
    async def test_refuses_entities_without_identity_rows(self, tmp_path):
        mongo, _ = _make_mongo_mock(
            counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER}
        )
        events: list[ProgressEvent] = []
        engine = DirectRestoreEngine(mongo, None, _collect_progress(events))

        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=self._reader(registry_rows=0),
        ), pytest.raises(RestoreEngineError, match="no registry identity rows"):
            await engine.run_restore(tmp_path / "kb.zip", "kb")

    @pytest.mark.asyncio
    async def test_override_completes_with_warning(self, tmp_path):
        mongo, _ = _make_mongo_mock(
            counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER}
        )
        events: list[ProgressEvent] = []
        engine = DirectRestoreEngine(mongo, None, _collect_progress(events))

        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=self._reader(registry_rows=0),
        ), patch("httpx.AsyncClient", return_value=self._httpx_ok()):
            await engine.run_restore(
                tmp_path / "kb.zip", "kb", allow_missing_identity=True
            )

        warnings = [e.message for e in events if e.phase == "warning"]
        assert any("NO registry identity rows" in w for w in warnings)
        assert events[-1].phase == "complete"

    @pytest.mark.asyncio
    async def test_identity_rows_present_passes_silently(self, tmp_path):
        mongo, _ = _make_mongo_mock(
            counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER}
        )
        events: list[ProgressEvent] = []
        engine = DirectRestoreEngine(mongo, None, _collect_progress(events))

        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=self._reader(registry_rows=2),
        ), patch("httpx.AsyncClient", return_value=self._httpx_ok()):
            await engine.run_restore(tmp_path / "kb.zip", "kb")

        assert not [e for e in events if e.phase == "warning"]
        assert events[-1].phase == "complete"

    @pytest.mark.asyncio
    async def test_derived_manifest_marker_surfaces_warning(self, tmp_path):
        """A derived_from key is unknown to the Manifest model (extras are
        ignored), so the engine reads the raw manifest to surface it."""
        mongo, _ = _make_mongo_mock(
            counts_per_collection={e: 0 for e in BACKUP_ENTITY_ORDER}
        )
        events: list[ProgressEvent] = []
        engine = DirectRestoreEngine(mongo, None, _collect_progress(events))

        raw = {"derived_from": {"transforms": ["filter-templates"]}}
        with patch(
            "document_store.services.backup_engine.ArchiveReader",
            return_value=self._reader(registry_rows=2, raw_manifest=raw),
        ), patch("httpx.AsyncClient", return_value=self._httpx_ok()):
            await engine.run_restore(tmp_path / "kb.zip", "kb")

        warnings = [e.message for e in events if e.phase == "warning"]
        assert any("DERIVED" in w for w in warnings)
        assert events[-1].phase == "complete"


class TestEndpointDeclarationDoorRepair:
    """CASE-830: the restore door makes edge-type endpoint declarations
    honest on the target.

    The remapper covers in-archive resolved halves via the id map; the door
    repairs everything else from the lookup half — in-archive-by-value from
    the archive's own rows (reserved identities are invisible to the
    Registry at this point), out-of-archive against the target's Registry —
    and clears what does not resolve, with a job warning. A stale
    source-install id persisted into a fresh namespace is a leak.
    """

    @staticmethod
    def _engine(events):
        return DirectRestoreEngine(MagicMock(), None, _collect_progress(events))

    @staticmethod
    def _plan(template_rows, id_map=None):
        from document_store.services.remap_restore import RemapPlan
        return RemapPlan(
            id_map={"templates": dict(id_map or {})},
            rows={"templates": template_rows},
        )

    @staticmethod
    def _remapper(template_map):
        from wip_archive.remap import IDRemapper
        r = IDRemapper()
        for old, new in template_map.items():
            r.add_template_mapping(old, new)
        return r

    @staticmethod
    def _httpx_lookup(status="not_found", entry_id=None):
        """Fake httpx.AsyncClient whose lookup-by-id answers one way."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "results": [{"status": status, "entry_id": entry_id}]
        }
        client = MagicMock()
        client.post = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    @pytest.mark.asyncio
    async def test_covered_entry_untouched_no_registry_call(self):
        events: list[ProgressEvent] = []
        engine = self._engine(events)
        remapper = self._remapper({"old-src-id": "new-tgt-id"})
        edge = {
            "template_id": "new-edge-id", "value": "EDGE",
            "usage": "relationship",
            "source_templates": [
                # Already rewritten by the remapper (in-archive endpoint).
                {"lookup_value": "MONSTER", "resolved": "new-tgt-id"}
            ],
            "target_templates": [],
        }
        plan = self._plan([edge])
        client = self._httpx_lookup()
        with patch("httpx.AsyncClient", return_value=client):
            await engine._repair_endpoint_declarations(
                {"src": "tgt"}, {"src": plan}, remapper
            )
        assert edge["source_templates"] == [
            {"lookup_value": "MONSTER", "resolved": "new-tgt-id"}
        ]
        client.post.assert_not_called()
        assert not [e for e in events if e.phase == "warning"]

    @pytest.mark.asyncio
    async def test_legacy_string_upgrades_from_archive_value_index(self):
        """A pre-3.1 value-form string names an archived template; the
        Registry cannot answer (reserved), so the archive's own rows do."""
        events: list[ProgressEvent] = []
        engine = self._engine(events)
        remapper = self._remapper({})
        monster = {
            "template_id": "new-monster-id", "value": "MONSTER",
            "usage": "entity",
        }
        edge = {
            "template_id": "new-edge-id", "value": "EDGE",
            "usage": "relationship",
            "source_templates": ["MONSTER"],
            "target_templates": [],
        }
        plan = self._plan([monster, edge])
        client = self._httpx_lookup()
        with patch("httpx.AsyncClient", return_value=client):
            await engine._repair_endpoint_declarations(
                {"src": "tgt"}, {"src": plan}, remapper
            )
        assert edge["source_templates"] == [
            {"lookup_value": "MONSTER", "resolved": "new-monster-id"}
        ]
        client.post.assert_not_called()
        assert not [e for e in events if e.phase == "warning"]

    @pytest.mark.asyncio
    async def test_out_of_archive_repairs_via_target_registry(self):
        events: list[ProgressEvent] = []
        engine = self._engine(events)
        remapper = self._remapper({})
        edge = {
            "template_id": "new-edge-id", "value": "EDGE",
            "usage": "relationship",
            "source_templates": [
                # Out-of-archive: resolved names the SOURCE install's id and
                # no map entry can exist for it.
                {"lookup_value": "wip:SESSION", "resolved": "old-foreign-id"}
            ],
            "target_templates": [],
        }
        plan = self._plan([edge])
        client = self._httpx_lookup(status="found", entry_id="tgt-session-id")
        with patch("httpx.AsyncClient", return_value=client):
            await engine._repair_endpoint_declarations(
                {"src": "tgt"}, {"src": plan}, remapper
            )
        assert edge["source_templates"] == [
            {"lookup_value": "wip:SESSION", "resolved": "tgt-session-id"}
        ]
        assert not [e for e in events if e.phase == "warning"]

    @pytest.mark.asyncio
    async def test_unresolvable_entry_nulled_with_warning_no_leak(self):
        events: list[ProgressEvent] = []
        engine = self._engine(events)
        remapper = self._remapper({})
        edge = {
            "template_id": "new-edge-id", "value": "EDGE",
            "usage": "relationship",
            "source_templates": [
                {"lookup_value": "GONE_TPL", "resolved": "old-foreign-id"}
            ],
            "target_templates": [],
        }
        plan = self._plan([edge])
        client = self._httpx_lookup(status="not_found")
        with patch("httpx.AsyncClient", return_value=client):
            await engine._repair_endpoint_declarations(
                {"src": "tgt"}, {"src": plan}, remapper
            )
        # The stale source id is gone (leak contract); the anchor survives.
        assert edge["source_templates"] == [
            {"lookup_value": "GONE_TPL", "resolved": None}
        ]
        warnings = [e.message for e in events if e.phase == "warning"]
        assert any("GONE_TPL" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_non_relationship_rows_ignored(self):
        events: list[ProgressEvent] = []
        engine = self._engine(events)
        entity = {
            "template_id": "t1", "value": "PLAIN", "usage": "entity",
        }
        plan = self._plan([entity])
        with patch("httpx.AsyncClient", return_value=self._httpx_lookup()):
            await engine._repair_endpoint_declarations(
                {"src": "tgt"}, {"src": plan}, self._remapper({})
            )
        assert "source_templates" not in entity
