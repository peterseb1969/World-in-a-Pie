"""
End-to-end tests: NATS event → SyncWorker → PostgreSQL row.

Tests the full pipeline with real NATS JetStream and real PostgreSQL.
Template Store HTTP calls are avoided by pre-populating the worker's
template cache.

Requires both POSTGRES_TEST_URI and NATS_TEST_URL env vars.
"""

import json

from reporting_sync.main import init_postgres_schema
from reporting_sync.models import SyncStatus
from reporting_sync.worker import SyncWorker

from .conftest import requires_e2e

# =============================================================================
# Helpers
# =============================================================================


def make_event(event_type: str, payload_key: str, payload: dict) -> bytes:
    """Build a NATS event message."""
    return json.dumps({
        "event_id": f"EVT-{event_type}",
        "event_type": event_type,
        "timestamp": "2026-01-15T10:00:00Z",
        payload_key: payload,
    }).encode()


SIMPLE_TEMPLATE = {
    "template_id": "TPL-E2E-001",
    "value": "E2EPerson",
    "version": 1,
    "status": "active",
    "namespace": "test",
    "fields": [
        {"name": "name", "type": "string"},
        {"name": "age", "type": "integer"},
        {"name": "category", "type": "term", "terminology_ref": "categories"},
    ],
}


# =============================================================================
# Document events: create → update → delete
# =============================================================================


@requires_e2e
class TestDocumentEventPipeline:
    """Full lifecycle: publish document events to NATS, verify PG state."""

    async def test_document_created_via_nats(self, pg_pool, nats_client):
        """Publish document.created → worker processes → row in PG."""
        nc, js, stream_name = nats_client
        await init_postgres_schema(pg_pool)

        # Create worker with real PG, real NATS
        status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
        worker = SyncWorker(nc, js, pg_pool, status)
        worker._template_cache["TPL-E2E-001"] = SIMPLE_TEMPLATE

        # Publish event
        event = make_event("document.created", "document", {
            "document_id": "DOC-E2E-001",
            "template_id": "TPL-E2E-001",
            "namespace": "test",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "hash-e2e-001",
            "data": {"name": "Alice", "age": 30, "category": "Staff"},
            "term_references": [{"field_path": "category", "term_id": "TERM-STAFF"}],
            "file_references": [],
            "created_at": "2026-01-15T10:00:00Z",
            "created_by": "test-user",
            "updated_at": None,
            "updated_by": None,
        })
        await js.publish("wip.documents", event)

        # Pull and process the message
        sub = await js.pull_subscribe("wip.>", durable="test-e2e", stream=stream_name)
        messages = await sub.fetch(batch=1, timeout=5)
        assert len(messages) == 1
        await worker._process_message(messages[0])

        # Verify in PostgreSQL
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT * FROM "doc_e2eperson" WHERE document_id = $1', "DOC-E2E-001"
            )
            assert row is not None
            assert row["name"] == "Alice"
            assert row["age"] == 30
            assert row["category"] == "Staff"
            assert row["category_term_id"] == "TERM-STAFF"
            assert row["namespace"] == "test"
            assert row["status"] == "active"

    async def test_document_updated_via_nats(self, pg_pool, nats_client):
        """Publish v1 then v2 → only v2 state in PG (LATEST_ONLY)."""
        nc, js, stream_name = nats_client
        await init_postgres_schema(pg_pool)

        status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
        worker = SyncWorker(nc, js, pg_pool, status)
        worker._template_cache["TPL-E2E-001"] = SIMPLE_TEMPLATE

        # Publish create then update
        for version, name, event_type in [(1, "Alice", "document.created"), (2, "Alice Updated", "document.updated")]:
            event = make_event(event_type, "document", {
                "document_id": "DOC-E2E-002",
                "template_id": "TPL-E2E-001",
                "namespace": "test",
                "template_version": 1,
                "version": version,
                "status": "active",
                "identity_hash": f"hash-e2e-002-v{version}",
                "data": {"name": name, "age": 30},
                "term_references": [],
                "file_references": [],
                "created_at": "2026-01-15T10:00:00Z",
                "created_by": "test-user",
                "updated_at": "2026-01-15T11:00:00Z" if version > 1 else None,
                "updated_by": "test-user" if version > 1 else None,
            })
            await js.publish("wip.documents", event)

        sub = await js.pull_subscribe("wip.>", durable="test-e2e-update", stream=stream_name)
        messages = await sub.fetch(batch=2, timeout=5)
        for msg in messages:
            await worker._process_message(msg)

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT name, version FROM "doc_e2eperson" WHERE document_id = $1', "DOC-E2E-002"
            )
            assert row["name"] == "Alice Updated"
            assert row["version"] == 2
            # Should be exactly 1 row (LATEST_ONLY)
            count = await conn.fetchval(
                'SELECT COUNT(*) FROM "doc_e2eperson" WHERE document_id = $1', "DOC-E2E-002"
            )
            assert count == 1

    async def test_document_deleted_via_nats(self, pg_pool, nats_client):
        """Publish create then delete → status set to 'deleted' in PG."""
        nc, js, stream_name = nats_client
        await init_postgres_schema(pg_pool)

        status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
        worker = SyncWorker(nc, js, pg_pool, status)
        worker._template_cache["TPL-E2E-001"] = SIMPLE_TEMPLATE

        # Create first
        create_event = make_event("document.created", "document", {
            "document_id": "DOC-E2E-003",
            "template_id": "TPL-E2E-001",
            "namespace": "test",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "hash-e2e-003",
            "data": {"name": "Bob"},
            "term_references": [],
            "file_references": [],
            "created_at": "2026-01-15T10:00:00Z",
            "created_by": "test-user",
            "updated_at": None,
            "updated_by": None,
        })
        await js.publish("wip.documents", create_event)

        # Then delete
        delete_event = make_event("document.deleted", "document", {
            "document_id": "DOC-E2E-003",
            "template_id": "TPL-E2E-001",
            "namespace": "test",
            "template_version": 1,
            "version": 1,
            "status": "deleted",
            "identity_hash": "hash-e2e-003",
            "data": {"name": "Bob"},
            "term_references": [],
            "file_references": [],
            "created_at": "2026-01-15T10:00:00Z",
            "created_by": "test-user",
            "updated_at": None,
            "updated_by": None,
        })
        await js.publish("wip.documents", delete_event)

        sub = await js.pull_subscribe("wip.>", durable="test-e2e-delete", stream=stream_name)
        messages = await sub.fetch(batch=2, timeout=5)
        for msg in messages:
            await worker._process_message(msg)

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT status FROM "doc_e2eperson" WHERE document_id = $1', "DOC-E2E-003"
            )
            assert row["status"] == "deleted"


# =============================================================================
# Template events
# =============================================================================


@requires_e2e
class TestTemplateEventPipeline:
    """Template events → metadata table + doc table schema."""

    async def test_template_created_via_nats(self, pg_pool, nats_client):
        """template.created → templates metadata row + doc table created."""
        nc, js, stream_name = nats_client
        await init_postgres_schema(pg_pool)

        status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
        worker = SyncWorker(nc, js, pg_pool, status)

        event = make_event("template.created", "template", {
            "template_id": "TPL-E2E-010",
            "value": "E2EReport",
            "version": 1,
            "status": "active",
            "namespace": "test",
            "label": "E2E Report",
            "description": "Test template",
            "fields": [
                {"name": "title", "type": "string"},
                {"name": "score", "type": "number"},
            ],
            "created_at": "2026-01-15T10:00:00Z",
            "created_by": "test-user",
        })
        await js.publish("wip.templates", event)

        sub = await js.pull_subscribe("wip.>", durable="test-e2e-tpl", stream=stream_name)
        messages = await sub.fetch(batch=1, timeout=5)
        await worker._process_message(messages[0])

        async with pg_pool.acquire() as conn:
            # Metadata table should have the template
            tpl = await conn.fetchrow(
                'SELECT * FROM templates WHERE template_id = $1 AND namespace = $2',
                "TPL-E2E-010", "test",
            )
            assert tpl is not None
            assert tpl["value"] == "E2EReport"
            assert tpl["status"] == "active"

            # Doc table should exist
            exists = await conn.fetchval(
                """SELECT EXISTS (SELECT FROM information_schema.tables
                   WHERE table_name = 'doc_e2ereport')"""
            )
            assert exists

    async def test_template_deleted_via_nats(self, pg_pool, nats_client):
        """template.deleted → status set to 'inactive' in metadata."""
        nc, js, stream_name = nats_client
        await init_postgres_schema(pg_pool)

        status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
        worker = SyncWorker(nc, js, pg_pool, status)

        # Create first
        create_event = make_event("template.created", "template", {
            "template_id": "TPL-E2E-011",
            "value": "E2ETemp",
            "version": 1,
            "status": "active",
            "namespace": "test",
            "fields": [{"name": "x", "type": "string"}],
            "created_at": "2026-01-15T10:00:00Z",
            "created_by": "test-user",
        })
        await js.publish("wip.templates", create_event)

        # Then delete
        delete_event = make_event("template.deleted", "template", {
            "template_id": "TPL-E2E-011",
            "value": "E2ETemp",
            "version": 1,
            "status": "deleted",
            "namespace": "test",
            "fields": [{"name": "x", "type": "string"}],
        })
        await js.publish("wip.templates", delete_event)

        sub = await js.pull_subscribe("wip.>", durable="test-e2e-tpl-del", stream=stream_name)
        messages = await sub.fetch(batch=2, timeout=5)
        for msg in messages:
            await worker._process_message(msg)

        async with pg_pool.acquire() as conn:
            tpl = await conn.fetchrow(
                'SELECT status FROM templates WHERE template_id = $1 AND namespace = $2',
                "TPL-E2E-011", "test",
            )
            assert tpl["status"] == "inactive"


# =============================================================================
# Terminology & term events
# =============================================================================


@requires_e2e
class TestTerminologyEventPipeline:
    """Terminology/term events → metadata tables."""

    async def test_terminology_created_via_nats(self, pg_pool, nats_client):
        nc, js, stream_name = nats_client
        await init_postgres_schema(pg_pool)

        status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
        worker = SyncWorker(nc, js, pg_pool, status)

        event = make_event("terminology.created", "terminology", {
            "terminology_id": "VOCAB-E2E-001",
            "namespace": "test",
            "value": "Gender",
            "label": "Gender",
            "case_sensitive": False,
            "allow_multiple": False,
            "extensible": True,
            "status": "active",
            "term_count": 3,
            "created_at": "2026-01-15T10:00:00Z",
            "created_by": "test-user",
        })
        await js.publish("wip.terminologies", event)

        sub = await js.pull_subscribe("wip.>", durable="test-e2e-vocab", stream=stream_name)
        messages = await sub.fetch(batch=1, timeout=5)
        await worker._process_message(messages[0])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT * FROM terminologies WHERE terminology_id = $1 AND namespace = $2',
                "VOCAB-E2E-001", "test",
            )
            assert row is not None
            assert row["value"] == "Gender"
            assert row["term_count"] == 3

    async def test_term_created_via_nats(self, pg_pool, nats_client):
        nc, js, stream_name = nats_client
        await init_postgres_schema(pg_pool)

        status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
        worker = SyncWorker(nc, js, pg_pool, status)

        event = make_event("term.created", "term", {
            "term_id": "TERM-E2E-001",
            "namespace": "test",
            "terminology_id": "VOCAB-E2E-001",
            "terminology_value": "Gender",
            "value": "Female",
            "aliases": ["F", "Fem"],
            "status": "active",
            "sort_order": 1,
            "created_at": "2026-01-15T10:00:00Z",
            "created_by": "test-user",
        })
        await js.publish("wip.terms", event)

        sub = await js.pull_subscribe("wip.>", durable="test-e2e-term", stream=stream_name)
        messages = await sub.fetch(batch=1, timeout=5)
        await worker._process_message(messages[0])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT * FROM terms WHERE term_id = $1 AND namespace = $2',
                "TERM-E2E-001", "test",
            )
            assert row is not None
            assert row["value"] == "Female"
            assert row["terminology_value"] == "Gender"

    async def test_term_hard_delete_pipeline(self, pg_pool, nats_client):
        """Insert a term, then hard delete it → row gone from PostgreSQL."""
        nc, js, stream_name = nats_client
        await init_postgres_schema(pg_pool)

        status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
        worker = SyncWorker(nc, js, pg_pool, status)

        # Create first
        create_event = make_event("term.created", "term", {
            "term_id": "TERM-E2E-HD",
            "namespace": "test",
            "terminology_id": "VOCAB-001",
            "terminology_value": "TestVocab",
            "value": "HardDeleteMe",
            "aliases": [],
            "status": "active",
            "sort_order": 0,
            "created_at": "2026-01-15T10:00:00Z",
            "created_by": "test-user",
        })
        await js.publish("wip.terms", create_event)

        # Then hard delete
        delete_event = make_event("term.deleted", "term", {
            "term_id": "TERM-E2E-HD",
            "namespace": "test",
            "hard_delete": True,
        })
        await js.publish("wip.terms", delete_event)

        sub = await js.pull_subscribe("wip.>", durable="test-e2e-term-hd", stream=stream_name)
        messages = await sub.fetch(batch=2, timeout=5)
        for msg in messages:
            await worker._process_message(msg)

        async with pg_pool.acquire() as conn:
            count = await conn.fetchval(
                'SELECT COUNT(*) FROM terms WHERE term_id = $1',
                "TERM-E2E-HD",
            )
            assert count == 0

    async def test_term_deprecated_via_nats(self, pg_pool, nats_client):
        """term.deprecated → status and reason updated in PG."""
        nc, js, stream_name = nats_client
        await init_postgres_schema(pg_pool)

        status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
        worker = SyncWorker(nc, js, pg_pool, status)

        # Create first
        create_event = make_event("term.created", "term", {
            "term_id": "TERM-E2E-DEP",
            "namespace": "test",
            "terminology_id": "VOCAB-001",
            "value": "OldTerm",
            "aliases": [],
            "status": "active",
            "sort_order": 0,
            "created_at": "2026-01-15T10:00:00Z",
            "created_by": "test-user",
        })
        await js.publish("wip.terms", create_event)

        # Deprecate
        dep_event = make_event("term.deprecated", "term", {
            "term_id": "TERM-E2E-DEP",
            "namespace": "test",
            "deprecated_reason": "Replaced by NewTerm",
            "replaced_by_term_id": "TERM-E2E-NEW",
        })
        dep_event_data = json.loads(dep_event)
        dep_event_data["changed_by"] = "admin"
        dep_event = json.dumps(dep_event_data).encode()
        await js.publish("wip.terms", dep_event)

        sub = await js.pull_subscribe("wip.>", durable="test-e2e-dep", stream=stream_name)
        messages = await sub.fetch(batch=2, timeout=5)
        for msg in messages:
            await worker._process_message(msg)

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT status, deprecated_reason, replaced_by_term_id FROM terms WHERE term_id = $1',
                "TERM-E2E-DEP",
            )
            assert row["status"] == "deprecated"
            assert row["deprecated_reason"] == "Replaced by NewTerm"
            assert row["replaced_by_term_id"] == "TERM-E2E-NEW"


# =============================================================================
# Relationship events
# =============================================================================


@requires_e2e
class TestRelationshipEventPipeline:
    """Relationship events → term_relationships table."""

    async def test_relationship_created_via_nats(self, pg_pool, nats_client):
        nc, js, stream_name = nats_client
        await init_postgres_schema(pg_pool)

        status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
        worker = SyncWorker(nc, js, pg_pool, status)

        event = make_event("relationship.created", "relationship", {
            "namespace": "test",
            "source_term_id": "TERM-A",
            "target_term_id": "TERM-B",
            "relationship_type": "is_a",
            "source_term_value": "Cat",
            "target_term_value": "Animal",
            "source_terminology_id": "VOCAB-1",
            "target_terminology_id": "VOCAB-1",
            "metadata": {"confidence": 0.95},
            "status": "active",
            "created_by": "test-user",
        })
        await js.publish("wip.relationships", event)

        sub = await js.pull_subscribe("wip.>", durable="test-e2e-rel", stream=stream_name)
        messages = await sub.fetch(batch=1, timeout=5)
        await worker._process_message(messages[0])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM term_relationships
                   WHERE source_term_id = $1 AND target_term_id = $2 AND namespace = $3""",
                "TERM-A", "TERM-B", "test",
            )
            assert row is not None
            assert row["relationship_type"] == "is_a"
            assert row["source_term_value"] == "Cat"
            assert row["status"] == "active"

    async def test_relationship_hard_delete_pipeline(self, pg_pool, nats_client):
        """Insert a relationship, then hard delete it → row gone from PostgreSQL."""
        nc, js, stream_name = nats_client
        await init_postgres_schema(pg_pool)

        status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
        worker = SyncWorker(nc, js, pg_pool, status)

        # Create
        create_event = make_event("relationship.created", "relationship", {
            "namespace": "test",
            "source_term_id": "TERM-HD-A",
            "target_term_id": "TERM-HD-B",
            "relationship_type": "is_a",
            "source_term_value": "Child",
            "target_term_value": "Parent",
            "source_terminology_id": "VOCAB-1",
            "target_terminology_id": "VOCAB-1",
            "metadata": {},
            "status": "active",
            "created_by": "test-user",
        })
        await js.publish("wip.relationships", create_event)

        # Hard delete
        delete_event = make_event("relationship.deleted", "relationship", {
            "namespace": "test",
            "source_term_id": "TERM-HD-A",
            "target_term_id": "TERM-HD-B",
            "relationship_type": "is_a",
            "hard_delete": True,
        })
        await js.publish("wip.relationships", delete_event)

        sub = await js.pull_subscribe("wip.>", durable="test-e2e-rel-hd", stream=stream_name)
        messages = await sub.fetch(batch=2, timeout=5)
        for msg in messages:
            await worker._process_message(msg)

        async with pg_pool.acquire() as conn:
            count = await conn.fetchval(
                """SELECT COUNT(*) FROM term_relationships
                   WHERE source_term_id = $1 AND target_term_id = $2 AND namespace = $3""",
                "TERM-HD-A", "TERM-HD-B", "test",
            )
            assert count == 0

    async def test_relationship_deleted_via_nats(self, pg_pool, nats_client):
        nc, js, stream_name = nats_client
        await init_postgres_schema(pg_pool)

        status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
        worker = SyncWorker(nc, js, pg_pool, status)

        # Create
        create_event = make_event("relationship.created", "relationship", {
            "namespace": "test",
            "source_term_id": "TERM-X",
            "target_term_id": "TERM-Y",
            "relationship_type": "part_of",
            "status": "active",
            "created_by": "test-user",
        })
        await js.publish("wip.relationships", create_event)

        # Delete
        delete_event = make_event("relationship.deleted", "relationship", {
            "namespace": "test",
            "source_term_id": "TERM-X",
            "target_term_id": "TERM-Y",
            "relationship_type": "part_of",
        })
        await js.publish("wip.relationships", delete_event)

        sub = await js.pull_subscribe("wip.>", durable="test-e2e-rel-del", stream=stream_name)
        messages = await sub.fetch(batch=2, timeout=5)
        for msg in messages:
            await worker._process_message(msg)

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT status FROM term_relationships
                   WHERE source_term_id = $1 AND target_term_id = $2 AND namespace = $3""",
                "TERM-X", "TERM-Y", "test",
            )
            assert row["status"] == "inactive"


# =============================================================================
# Worker status tracking
# =============================================================================


@requires_e2e
class TestWorkerStatusTracking:
    """Verify worker status counters update during E2E processing."""

    async def test_events_processed_counter(self, pg_pool, nats_client):
        """Worker status tracks processed event count."""
        nc, js, stream_name = nats_client
        await init_postgres_schema(pg_pool)

        status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
        worker = SyncWorker(nc, js, pg_pool, status)

        # Publish a terminology event (simple, no template fetch needed)
        event = make_event("terminology.created", "terminology", {
            "terminology_id": "VOCAB-STATUS-001",
            "namespace": "test",
            "value": "StatusTest",
            "status": "active",
            "created_at": "2026-01-15T10:00:00Z",
            "created_by": "test-user",
        })
        await js.publish("wip.terminologies", event)

        assert status.events_processed == 0

        sub = await js.pull_subscribe("wip.>", durable="test-e2e-status", stream=stream_name)
        messages = await sub.fetch(batch=1, timeout=5)
        await worker._process_message(messages[0])

        assert status.events_processed == 1
        assert status.last_event_processed is not None
