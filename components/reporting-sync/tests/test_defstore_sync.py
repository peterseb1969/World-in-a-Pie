"""
Tests for terminology, term, and relation event processing in SyncWorker.

Covers the def-store → PostgreSQL sync path added for ontology support.
All external dependencies (NATS, PostgreSQL, httpx) are mocked.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from reporting_sync.models import SyncStatus
from reporting_sync.worker import SyncWorker

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def mock_pool():
    """Mock asyncpg pool with async context manager support."""
    pool = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])

    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm

    return pool, conn


@pytest.fixture
def worker(mock_pool):
    """SyncWorker with mocked dependencies."""
    pool, _conn = mock_pool
    status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
    js = MagicMock()
    js.pull_subscribe = AsyncMock()
    nc = MagicMock()
    w = SyncWorker(nc, js, pool, status)
    w.schema_manager.table_exists = AsyncMock(return_value=False)
    w.schema_manager.create_table = AsyncMock(return_value="CREATE TABLE ...")
    w.schema_manager.ensure_table_for_template = AsyncMock(return_value="doc_person")
    w.schema_manager.ensure_terminologies_table = AsyncMock(return_value="terminologies")
    w.schema_manager.ensure_terms_table = AsyncMock(return_value="terms")
    w.schema_manager.ensure_term_relations_table = AsyncMock(return_value="term_relations")
    w.schema_manager.update_table_schema = AsyncMock(return_value=[])
    return w


def _make_nats_message(event_data: dict) -> MagicMock:
    """Create a mock NATS message with JSON data."""
    msg = MagicMock()
    msg.data = json.dumps(event_data).encode()
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    return msg


# =========================================================================
# Terminology Event Processing
# =========================================================================


class TestTerminologyEvents:
    """Tests for _process_terminology_event."""

    def _make_event(self, event_type="terminology.created", **overrides):
        terminology = {
            "terminology_id": "TRM-001",
            "namespace": "wip",
            "value": "COUNTRIES",
            "label": "Countries",
            "description": "Country list",
            "case_sensitive": False,
            "allow_multiple": False,
            "extensible": True,
            "status": "active",
            "term_count": 42,
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "admin",
            "updated_at": "2024-01-30T10:00:00Z",
            "updated_by": "admin",
        }
        terminology.update(overrides)
        return {"event_type": event_type, "terminology": terminology}

    @pytest.mark.asyncio
    async def test_create_event_upserts(self, worker, mock_pool):
        """terminology.created ensures table and inserts row."""
        _pool, conn = mock_pool
        event = self._make_event("terminology.created")

        result = await worker._process_terminology_event(event)

        assert result is True
        worker.schema_manager.ensure_terminologies_table.assert_awaited_once()
        conn.execute.assert_awaited_once()
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_create_event_arg_types(self, worker, mock_pool):
        """Positional args to conn.execute have correct types for asyncpg."""
        _pool, conn = mock_pool
        event = self._make_event("terminology.created")

        await worker._process_terminology_event(event)

        args = conn.execute.call_args[0]
        # $6=case_sensitive, $7=allow_multiple, $8=extensible, $9=mutable (booleans)
        assert isinstance(args[6], bool), f"case_sensitive: expected bool, got {type(args[6])}"
        assert isinstance(args[7], bool), f"allow_multiple: expected bool, got {type(args[7])}"
        assert isinstance(args[8], bool), f"extensible: expected bool, got {type(args[8])}"
        assert isinstance(args[9], bool), f"mutable: expected bool, got {type(args[9])}"
        # $11=term_count (int)
        assert isinstance(args[11], int), f"term_count: expected int, got {type(args[11])}"
        # $12=created_at, $14=updated_at (datetime)
        assert isinstance(args[12], datetime), f"created_at: expected datetime, got {type(args[12])}"
        assert isinstance(args[14], datetime), f"updated_at: expected datetime, got {type(args[14])}"

    @pytest.mark.asyncio
    async def test_update_event_upserts(self, worker, mock_pool):
        """terminology.updated uses the same upsert path."""
        _pool, conn = mock_pool
        event = self._make_event("terminology.updated")

        result = await worker._process_terminology_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_delete_event_sets_inactive(self, worker, mock_pool):
        """terminology.deleted updates status to inactive."""
        _pool, conn = mock_pool
        event = self._make_event("terminology.deleted")
        event["changed_by"] = "admin"

        result = await worker._process_terminology_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "inactive" in sql

    @pytest.mark.asyncio
    async def test_terminology_hard_delete(self, worker, mock_pool):
        """terminology.deleted with hard_delete=True removes row via DELETE."""
        _pool, conn = mock_pool
        event = self._make_event("terminology.deleted", hard_delete=True)

        result = await worker._process_terminology_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "DELETE FROM" in sql
        assert '"terminologies"' in sql

    @pytest.mark.asyncio
    async def test_terminology_soft_delete_unchanged(self, worker, mock_pool):
        """terminology.deleted WITHOUT hard_delete preserves UPDATE behavior."""
        _pool, conn = mock_pool
        event = self._make_event("terminology.deleted")
        event["changed_by"] = "admin"

        result = await worker._process_terminology_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "inactive" in sql
        assert "DELETE" not in sql

    @pytest.mark.asyncio
    async def test_missing_terminology_id_returns_false(self, worker):
        """Event without terminology_id returns False."""
        event = {"event_type": "terminology.created", "terminology": {"value": "X", "namespace": "wip"}}
        result = await worker._process_terminology_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_terminology_message_ack(self, worker, mock_pool):
        """Full message processing: terminology event acks the NATS message."""
        _pool, _conn = mock_pool
        event = self._make_event()
        msg = _make_nats_message(event)

        await worker._process_message(msg)

        msg.ack.assert_awaited_once()
        assert worker.status.events_processed == 1

    @pytest.mark.asyncio
    async def test_db_error_propagates(self, worker, mock_pool):
        """Database error during terminology sync raises."""
        _pool, conn = mock_pool
        conn.execute = AsyncMock(side_effect=Exception("connection lost"))
        event = self._make_event()

        with pytest.raises(Exception, match="connection lost"):
            await worker._process_terminology_event(event)


# =========================================================================
# Term Event Processing
# =========================================================================


class TestTermEvents:
    """Tests for _process_term_event."""

    def _make_event(self, event_type="term.created", **overrides):
        term = {
            "term_id": "0190b000-0000-7000-0000-000000000001",
            "namespace": "wip",
            "terminology_id": "TRM-001",
            "terminology_value": "COUNTRIES",
            "value": "United Kingdom",
            "aliases": ["UK", "GB"],
            "label": "United Kingdom",
            "description": "Country in Europe",
            "sort_order": 1,
            "parent_term_id": None,
            "status": "active",
            "deprecated_reason": None,
            "replaced_by_term_id": None,
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "admin",
            "updated_at": "2024-01-30T10:00:00Z",
            "updated_by": "admin",
        }
        term.update(overrides)
        return {"event_type": event_type, "term": term}

    @pytest.mark.asyncio
    async def test_create_event_upserts(self, worker, mock_pool):
        """term.created ensures table and upserts row."""
        _pool, conn = mock_pool
        event = self._make_event("term.created")

        result = await worker._process_term_event(event)

        assert result is True
        worker.schema_manager.ensure_terms_table.assert_awaited_once()
        conn.execute.assert_awaited_once()
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_create_event_arg_types(self, worker, mock_pool):
        """Positional args to conn.execute have correct types for asyncpg."""
        _pool, conn = mock_pool
        event = self._make_event("term.created")

        await worker._process_term_event(event)

        args = conn.execute.call_args[0]
        # $6=aliases (JSON string)
        assert isinstance(args[6], str), f"aliases: expected JSON str, got {type(args[6])}"
        assert json.loads(args[6]) == ["UK", "GB"]  # valid JSON
        # $9=sort_order (int)
        assert isinstance(args[9], int), f"sort_order: expected int, got {type(args[9])}"
        # $14=created_at, $16=updated_at (datetime)
        assert isinstance(args[14], datetime), f"created_at: expected datetime, got {type(args[14])}"
        assert isinstance(args[16], datetime), f"updated_at: expected datetime, got {type(args[16])}"

    @pytest.mark.asyncio
    async def test_update_event_upserts(self, worker, mock_pool):
        """term.updated uses the same upsert path."""
        _pool, conn = mock_pool
        event = self._make_event("term.updated")

        result = await worker._process_term_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO" in sql

    @pytest.mark.asyncio
    async def test_delete_event_sets_inactive(self, worker, mock_pool):
        """term.deleted updates status to inactive."""
        _pool, conn = mock_pool
        event = self._make_event("term.deleted")
        event["changed_by"] = "admin"

        result = await worker._process_term_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "inactive" in sql

    @pytest.mark.asyncio
    async def test_deprecated_event_sets_deprecated(self, worker, mock_pool):
        """term.deprecated updates status and sets reason and replacement."""
        _pool, conn = mock_pool
        event = self._make_event(
            "term.deprecated",
            deprecated_reason="Replaced by ISO code",
            replaced_by_term_id="0190b000-0000-7000-0000-000000000002",
        )
        event["changed_by"] = "admin"

        result = await worker._process_term_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "deprecated" in sql

    @pytest.mark.asyncio
    async def test_term_hard_delete(self, worker, mock_pool):
        """term.deleted with hard_delete=True removes row via DELETE."""
        _pool, conn = mock_pool
        event = self._make_event("term.deleted", hard_delete=True)

        result = await worker._process_term_event(event)

        assert result is True
        # Hard delete calls execute twice: first relations, then terms
        calls = conn.execute.call_args_list
        assert len(calls) == 2
        terms_sql = calls[1][0][0]
        assert "DELETE FROM" in terms_sql
        assert '"terms"' in terms_sql

    @pytest.mark.asyncio
    async def test_term_hard_delete_cascades_relations(self, worker, mock_pool):
        """term.deleted with hard_delete=True also deletes from term_relations."""
        _pool, conn = mock_pool
        event = self._make_event("term.deleted", hard_delete=True)

        result = await worker._process_term_event(event)

        assert result is True
        calls = conn.execute.call_args_list
        assert len(calls) == 2
        rel_sql = calls[0][0][0]
        assert "DELETE FROM" in rel_sql
        assert '"term_relations"' in rel_sql
        terms_sql = calls[1][0][0]
        assert "DELETE FROM" in terms_sql
        assert '"terms"' in terms_sql

    @pytest.mark.asyncio
    async def test_term_soft_delete_unchanged(self, worker, mock_pool):
        """term.deleted WITHOUT hard_delete preserves UPDATE behavior."""
        _pool, conn = mock_pool
        event = self._make_event("term.deleted")
        event["changed_by"] = "admin"

        result = await worker._process_term_event(event)

        assert result is True
        conn.execute.assert_awaited_once()
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "inactive" in sql
        assert "DELETE" not in sql

    @pytest.mark.asyncio
    async def test_missing_term_id_returns_false(self, worker):
        """Event without term_id returns False."""
        event = {"event_type": "term.created", "term": {"value": "X", "namespace": "wip"}}
        result = await worker._process_term_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_aliases_serialized_as_json(self, worker, mock_pool):
        """Aliases list is JSON-serialized before insert."""
        _pool, conn = mock_pool
        event = self._make_event("term.created", aliases=["UK", "GB", "Britain"])

        await worker._process_term_event(event)

        # Find the aliases argument (position 5, 0-indexed in the call args)
        args = conn.execute.call_args[0]
        # The aliases should be a JSON string somewhere in the args
        aliases_json = json.dumps(["UK", "GB", "Britain"])
        assert aliases_json in args, f"Expected serialized aliases in args: {args}"

    @pytest.mark.asyncio
    async def test_term_message_routes_correctly(self, worker, mock_pool):
        """term.* events route to _process_term_event via _process_message."""
        _pool, _conn = mock_pool
        event = self._make_event()
        msg = _make_nats_message(event)

        await worker._process_message(msg)

        msg.ack.assert_awaited_once()
        worker.schema_manager.ensure_terms_table.assert_awaited_once()


# =========================================================================
# Relation Event Processing
# =========================================================================


class TestRelationEvents:
    """Tests for _process_term_relation_event."""

    def _make_event(self, event_type="term_relation.created", **overrides):
        rel = {
            "namespace": "wip",
            "source_term_id": "0190b000-0000-7000-0000-000000000001",
            "target_term_id": "0190b000-0000-7000-0000-000000000002",
            "relation_type": "is_a",
            "source_term_value": "Pneumonia",
            "target_term_value": "Lung Disease",
            "source_terminology_id": "TRM-001",
            "target_terminology_id": "TRM-001",
            "metadata": {"source_ontology": "SNOMED"},
            "status": "active",
            "created_by": "admin",
        }
        rel.update(overrides)
        return {"event_type": event_type, "relation": rel}

    @pytest.mark.asyncio
    async def test_create_event_upserts(self, worker, mock_pool):
        """term_relation.created ensures table and upserts row."""
        _pool, conn = mock_pool
        event = self._make_event("term_relation.created")

        result = await worker._process_term_relation_event(event)

        assert result is True
        worker.schema_manager.ensure_term_relations_table.assert_awaited_once()
        conn.execute.assert_awaited_once()
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_create_event_arg_types(self, worker, mock_pool):
        """Positional args to conn.execute have correct types for asyncpg."""
        _pool, conn = mock_pool
        event = self._make_event("term_relation.created")

        await worker._process_term_relation_event(event)

        args = conn.execute.call_args[0]
        # $9=metadata (JSON string, not dict)
        assert isinstance(args[9], str), f"metadata: expected JSON str, got {type(args[9])}"
        parsed = json.loads(args[9])
        assert isinstance(parsed, dict)

    @pytest.mark.asyncio
    async def test_delete_event_sets_inactive(self, worker, mock_pool):
        """term_relation.deleted updates status to inactive."""
        _pool, conn = mock_pool
        event = self._make_event("term_relation.deleted")

        result = await worker._process_term_relation_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "inactive" in sql

    @pytest.mark.asyncio
    async def test_relation_hard_delete(self, worker, mock_pool):
        """term_relation.deleted with hard_delete=True removes row via DELETE."""
        _pool, conn = mock_pool
        event = self._make_event("term_relation.deleted", hard_delete=True)

        result = await worker._process_term_relation_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "DELETE FROM" in sql
        assert '"term_relations"' in sql

    @pytest.mark.asyncio
    async def test_relation_soft_delete_unchanged(self, worker, mock_pool):
        """term_relation.deleted WITHOUT hard_delete preserves UPDATE behavior."""
        _pool, conn = mock_pool
        event = self._make_event("term_relation.deleted")

        result = await worker._process_term_relation_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "inactive" in sql
        assert "DELETE" not in sql

    @pytest.mark.asyncio
    async def test_missing_source_returns_false(self, worker):
        """Event without source_term_id returns False."""
        event = self._make_event(source_term_id=None)
        result = await worker._process_term_relation_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_target_returns_false(self, worker):
        """Event without target_term_id returns False."""
        event = self._make_event(target_term_id=None)
        result = await worker._process_term_relation_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_type_returns_false(self, worker):
        """Event without relation_type returns False."""
        event = self._make_event(relation_type=None)
        result = await worker._process_term_relation_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_metadata_serialized_as_json(self, worker, mock_pool):
        """Metadata dict is JSON-serialized before insert."""
        _pool, conn = mock_pool
        meta = {"source_ontology": "SNOMED", "confidence": 0.99}
        event = self._make_event(metadata=meta)

        await worker._process_term_relation_event(event)

        args = conn.execute.call_args[0]
        meta_json = json.dumps(meta)
        assert meta_json in args, f"Expected serialized metadata in args: {args}"

    @pytest.mark.asyncio
    async def test_relation_message_routes_correctly(self, worker, mock_pool):
        """relation.* events route to _process_term_relation_event."""
        _pool, _conn = mock_pool
        event = self._make_event()
        msg = _make_nats_message(event)

        await worker._process_message(msg)

        msg.ack.assert_awaited_once()
        worker.schema_manager.ensure_term_relations_table.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_db_error_propagates(self, worker, mock_pool):
        """Database error during relation sync raises."""
        _pool, conn = mock_pool
        conn.execute = AsyncMock(side_effect=RuntimeError("db gone"))
        event = self._make_event()

        with pytest.raises(RuntimeError, match="db gone"):
            await worker._process_term_relation_event(event)

    @pytest.mark.asyncio
    async def test_create_passes_all_fields(self, worker, mock_pool):
        """Verify all relation fields are passed to the INSERT."""
        _pool, conn = mock_pool
        event = self._make_event(
            source_term_id="S1",
            target_term_id="T1",
            relation_type="maps_to",
            source_term_value="Source Val",
            target_term_value="Target Val",
            source_terminology_id="TRM-A",
            target_terminology_id="TRM-B",
            created_by="test-user",
        )

        await worker._process_term_relation_event(event)

        args = conn.execute.call_args[0]
        # Check key positional args are present
        assert "S1" in args
        assert "T1" in args
        assert "maps_to" in args
        assert "Source Val" in args
        assert "Target Val" in args
        assert "TRM-A" in args
        assert "TRM-B" in args
        assert "test-user" in args


# =========================================================================
# Schema Manager - Table Creation
# =========================================================================


class TestSchemaManagerTables:
    """Tests for ensure_*_table methods on SchemaManager."""

    @pytest.mark.asyncio
    async def test_ensure_terminologies_table_creates_ddl(self, mock_pool):
        """ensure_terminologies_table creates table with correct schema."""
        from reporting_sync.schema_manager import SchemaManager

        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=False)  # table doesn't exist
        sm = SchemaManager(pool)

        result = await sm.ensure_terminologies_table()

        assert result == "terminologies"
        conn.execute.assert_awaited_once()
        ddl = conn.execute.call_args[0][0]
        assert "CREATE TABLE" in ddl
        assert '"terminology_id"' in ddl
        assert '"namespace"' in ddl
        assert "PRIMARY KEY" in ddl

    @pytest.mark.asyncio
    async def test_ensure_terms_table_creates_ddl(self, mock_pool):
        """ensure_terms_table creates table with correct schema."""
        from reporting_sync.schema_manager import SchemaManager

        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=False)
        sm = SchemaManager(pool)

        result = await sm.ensure_terms_table()

        assert result == "terms"
        conn.execute.assert_awaited_once()
        ddl = conn.execute.call_args[0][0]
        assert "CREATE TABLE" in ddl
        assert '"term_id"' in ddl
        assert '"terminology_id"' in ddl
        assert '"aliases"' in ddl

    @pytest.mark.asyncio
    async def test_ensure_term_relations_table_creates_ddl(self, mock_pool):
        """ensure_term_relations_table creates table with correct schema."""
        from reporting_sync.schema_manager import SchemaManager

        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=False)
        sm = SchemaManager(pool)

        result = await sm.ensure_term_relations_table()

        assert result == "term_relations"
        conn.execute.assert_awaited_once()
        ddl = conn.execute.call_args[0][0]
        assert "CREATE TABLE" in ddl
        assert '"source_term_id"' in ddl
        assert '"target_term_id"' in ddl
        assert '"relation_type"' in ddl
        assert "PRIMARY KEY" in ddl

    @pytest.mark.asyncio
    async def test_ensure_terminologies_table_skips_if_exists(self, mock_pool):
        """If table already exists, no DDL is executed."""
        from reporting_sync.schema_manager import SchemaManager

        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=True)  # table exists
        sm = SchemaManager(pool)

        result = await sm.ensure_terminologies_table()

        assert result == "terminologies"
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ensure_terms_table_skips_if_exists(self, mock_pool):
        """If table already exists, no DDL is executed."""
        from reporting_sync.schema_manager import SchemaManager

        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=True)
        sm = SchemaManager(pool)

        result = await sm.ensure_terms_table()

        assert result == "terms"
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ensure_relations_table_skips_if_exists(self, mock_pool):
        """If table already exists, no DDL is executed."""
        from reporting_sync.schema_manager import SchemaManager

        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=True)
        sm = SchemaManager(pool)

        result = await sm.ensure_term_relations_table()

        assert result == "term_relations"
        conn.execute.assert_not_awaited()
