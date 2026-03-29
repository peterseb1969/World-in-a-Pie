"""
Tests for terminology, term, and relationship event processing in SyncWorker.

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
    pool, conn = mock_pool
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
    w.schema_manager.ensure_term_relationships_table = AsyncMock(return_value="term_relationships")
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
        pool, conn = mock_pool
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
        pool, conn = mock_pool
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
        pool, conn = mock_pool
        event = self._make_event("terminology.updated")

        result = await worker._process_terminology_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_delete_event_sets_inactive(self, worker, mock_pool):
        """terminology.deleted updates status to inactive."""
        pool, conn = mock_pool
        event = self._make_event("terminology.deleted")
        event["changed_by"] = "admin"

        result = await worker._process_terminology_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "inactive" in sql

    @pytest.mark.asyncio
    async def test_missing_terminology_id_returns_false(self, worker):
        """Event without terminology_id returns False."""
        event = {"event_type": "terminology.created", "terminology": {"value": "X"}}
        result = await worker._process_terminology_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_terminology_message_ack(self, worker, mock_pool):
        """Full message processing: terminology event acks the NATS message."""
        pool, conn = mock_pool
        event = self._make_event()
        msg = _make_nats_message(event)

        await worker._process_message(msg)

        msg.ack.assert_awaited_once()
        assert worker.status.events_processed == 1

    @pytest.mark.asyncio
    async def test_db_error_propagates(self, worker, mock_pool):
        """Database error during terminology sync raises."""
        pool, conn = mock_pool
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
            "term_id": "T-001",
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
        pool, conn = mock_pool
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
        pool, conn = mock_pool
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
        pool, conn = mock_pool
        event = self._make_event("term.updated")

        result = await worker._process_term_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO" in sql

    @pytest.mark.asyncio
    async def test_delete_event_sets_inactive(self, worker, mock_pool):
        """term.deleted updates status to inactive."""
        pool, conn = mock_pool
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
        pool, conn = mock_pool
        event = self._make_event(
            "term.deprecated",
            deprecated_reason="Replaced by ISO code",
            replaced_by_term_id="T-002",
        )
        event["changed_by"] = "admin"

        result = await worker._process_term_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "deprecated" in sql

    @pytest.mark.asyncio
    async def test_missing_term_id_returns_false(self, worker):
        """Event without term_id returns False."""
        event = {"event_type": "term.created", "term": {"value": "X"}}
        result = await worker._process_term_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_aliases_serialized_as_json(self, worker, mock_pool):
        """Aliases list is JSON-serialized before insert."""
        pool, conn = mock_pool
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
        pool, conn = mock_pool
        event = self._make_event()
        msg = _make_nats_message(event)

        await worker._process_message(msg)

        msg.ack.assert_awaited_once()
        worker.schema_manager.ensure_terms_table.assert_awaited_once()


# =========================================================================
# Relationship Event Processing
# =========================================================================


class TestRelationshipEvents:
    """Tests for _process_relationship_event."""

    def _make_event(self, event_type="relationship.created", **overrides):
        rel = {
            "namespace": "wip",
            "source_term_id": "T-001",
            "target_term_id": "T-002",
            "relationship_type": "is_a",
            "source_term_value": "Pneumonia",
            "target_term_value": "Lung Disease",
            "source_terminology_id": "TRM-001",
            "target_terminology_id": "TRM-001",
            "metadata": {"source_ontology": "SNOMED"},
            "status": "active",
            "created_by": "admin",
        }
        rel.update(overrides)
        return {"event_type": event_type, "relationship": rel}

    @pytest.mark.asyncio
    async def test_create_event_upserts(self, worker, mock_pool):
        """relationship.created ensures table and upserts row."""
        pool, conn = mock_pool
        event = self._make_event("relationship.created")

        result = await worker._process_relationship_event(event)

        assert result is True
        worker.schema_manager.ensure_term_relationships_table.assert_awaited_once()
        conn.execute.assert_awaited_once()
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_create_event_arg_types(self, worker, mock_pool):
        """Positional args to conn.execute have correct types for asyncpg."""
        pool, conn = mock_pool
        event = self._make_event("relationship.created")

        await worker._process_relationship_event(event)

        args = conn.execute.call_args[0]
        # $9=metadata (JSON string, not dict)
        assert isinstance(args[9], str), f"metadata: expected JSON str, got {type(args[9])}"
        parsed = json.loads(args[9])
        assert isinstance(parsed, dict)

    @pytest.mark.asyncio
    async def test_delete_event_sets_inactive(self, worker, mock_pool):
        """relationship.deleted updates status to inactive."""
        pool, conn = mock_pool
        event = self._make_event("relationship.deleted")

        result = await worker._process_relationship_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "inactive" in sql

    @pytest.mark.asyncio
    async def test_missing_source_returns_false(self, worker):
        """Event without source_term_id returns False."""
        event = self._make_event(source_term_id=None)
        result = await worker._process_relationship_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_target_returns_false(self, worker):
        """Event without target_term_id returns False."""
        event = self._make_event(target_term_id=None)
        result = await worker._process_relationship_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_type_returns_false(self, worker):
        """Event without relationship_type returns False."""
        event = self._make_event(relationship_type=None)
        result = await worker._process_relationship_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_metadata_serialized_as_json(self, worker, mock_pool):
        """Metadata dict is JSON-serialized before insert."""
        pool, conn = mock_pool
        meta = {"source_ontology": "SNOMED", "confidence": 0.99}
        event = self._make_event(metadata=meta)

        await worker._process_relationship_event(event)

        args = conn.execute.call_args[0]
        meta_json = json.dumps(meta)
        assert meta_json in args, f"Expected serialized metadata in args: {args}"

    @pytest.mark.asyncio
    async def test_relationship_message_routes_correctly(self, worker, mock_pool):
        """relationship.* events route to _process_relationship_event."""
        pool, conn = mock_pool
        event = self._make_event()
        msg = _make_nats_message(event)

        await worker._process_message(msg)

        msg.ack.assert_awaited_once()
        worker.schema_manager.ensure_term_relationships_table.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_db_error_propagates(self, worker, mock_pool):
        """Database error during relationship sync raises."""
        pool, conn = mock_pool
        conn.execute = AsyncMock(side_effect=RuntimeError("db gone"))
        event = self._make_event()

        with pytest.raises(RuntimeError, match="db gone"):
            await worker._process_relationship_event(event)

    @pytest.mark.asyncio
    async def test_create_passes_all_fields(self, worker, mock_pool):
        """Verify all relationship fields are passed to the INSERT."""
        pool, conn = mock_pool
        event = self._make_event(
            source_term_id="S1",
            target_term_id="T1",
            relationship_type="maps_to",
            source_term_value="Source Val",
            target_term_value="Target Val",
            source_terminology_id="TRM-A",
            target_terminology_id="TRM-B",
            created_by="test-user",
        )

        await worker._process_relationship_event(event)

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
    async def test_ensure_term_relationships_table_creates_ddl(self, mock_pool):
        """ensure_term_relationships_table creates table with correct schema."""
        from reporting_sync.schema_manager import SchemaManager

        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=False)
        sm = SchemaManager(pool)

        result = await sm.ensure_term_relationships_table()

        assert result == "term_relationships"
        conn.execute.assert_awaited_once()
        ddl = conn.execute.call_args[0][0]
        assert "CREATE TABLE" in ddl
        assert '"source_term_id"' in ddl
        assert '"target_term_id"' in ddl
        assert '"relationship_type"' in ddl
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
    async def test_ensure_relationships_table_skips_if_exists(self, mock_pool):
        """If table already exists, no DDL is executed."""
        from reporting_sync.schema_manager import SchemaManager

        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=True)
        sm = SchemaManager(pool)

        result = await sm.ensure_term_relationships_table()

        assert result == "term_relationships"
        conn.execute.assert_not_awaited()
