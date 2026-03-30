"""
E2E entity lifecycle tests: create → update → archive/delete for every entity type.

Each test class walks a single entity through its full lifecycle via NATS events,
verifying PostgreSQL state at every step.  This is the test that would have caught
the missing document.archived handler.

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

_durable_counter = 0


def _next_durable(prefix: str) -> str:
    """Unique durable name so NATS consumers don't collide between tests."""
    global _durable_counter
    _durable_counter += 1
    return f"{prefix}-{_durable_counter}"


def make_event(event_type: str, payload_key: str, payload: dict) -> bytes:
    return json.dumps({
        "event_id": f"EVT-LC-{event_type}",
        "event_type": event_type,
        "timestamp": "2026-01-20T10:00:00Z",
        payload_key: payload,
    }).encode()


def make_event_with_extras(event_type: str, payload_key: str, payload: dict, **extras) -> bytes:
    """Build event with top-level extra fields (e.g. changed_by)."""
    msg = {
        "event_id": f"EVT-LC-{event_type}",
        "event_type": event_type,
        "timestamp": "2026-01-20T10:00:00Z",
        payload_key: payload,
        **extras,
    }
    return json.dumps(msg).encode()


async def _make_worker(pg_pool, nats_client, template_cache=None):
    """Create a SyncWorker with real PG and NATS, plus a durable consumer.

    Returns (worker, js, sub) — the sub is reused across all steps in a test
    so that each fetch only gets new messages.
    """
    nc, js, stream_name = nats_client
    await init_postgres_schema(pg_pool)
    status = SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)
    worker = SyncWorker(nc, js, pg_pool, status)
    if template_cache:
        worker._template_cache.update(template_cache)
    durable = _next_durable("lifecycle")
    sub = await js.pull_subscribe("wip.>", durable=durable, stream=stream_name)
    return worker, js, sub


async def _publish_and_process(worker, js, sub, subject: str, events: list[bytes]):
    """Publish events to a NATS subject, pull from the shared consumer, process."""
    for event in events:
        await js.publish(subject, event)
    messages = await sub.fetch(batch=len(events), timeout=5)
    assert len(messages) == len(events)
    for msg in messages:
        await worker._process_message(msg)


# =============================================================================
# Document lifecycle: create → update → archive → delete
# =============================================================================

LIFECYCLE_TEMPLATE = {
    "template_id": "TPL-LC-001",
    "value": "LCPerson",
    "version": 1,
    "status": "active",
    "namespace": "test",
    "fields": [
        {"name": "name", "type": "string"},
        {"name": "age", "type": "integer"},
        {"name": "role", "type": "string"},
    ],
}


def _doc_payload(doc_id, version, status, data, **overrides):
    base = {
        "document_id": doc_id,
        "template_id": "TPL-LC-001",
        "namespace": "test",
        "template_version": 1,
        "version": version,
        "status": status,
        "identity_hash": f"hash-{doc_id}-v{version}",
        "data": data,
        "term_references": [],
        "file_references": [],
        "created_at": "2026-01-20T10:00:00Z",
        "created_by": "test-user",
        "updated_at": "2026-01-20T11:00:00Z" if version > 1 else None,
        "updated_by": "test-user" if version > 1 else None,
    }
    base.update(overrides)
    return base


@requires_e2e
class TestDocumentLifecycle:
    """Document: create → update → archive → delete, verifying PG at each step."""

    async def test_full_lifecycle(self, pg_pool, nats_client):
        worker, js, sub = await _make_worker(
            pg_pool, nats_client,
            template_cache={"TPL-LC-001": LIFECYCLE_TEMPLATE},
        )

        # --- Step 1: Create ---
        await _publish_and_process(worker, js, sub, "wip.documents", [
            make_event("document.created", "document",
                       _doc_payload("DOC-LC-001", 1, "active", {"name": "Alice", "age": 30, "role": "engineer"})),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT * FROM "doc_lcperson" WHERE document_id = $1', "DOC-LC-001"
            )
            assert row is not None
            assert row["name"] == "Alice"
            assert row["age"] == 30
            assert row["status"] == "active"

        # --- Step 2: Update ---
        await _publish_and_process(worker, js, sub, "wip.documents", [
            make_event("document.updated", "document",
                       _doc_payload("DOC-LC-001", 2, "active", {"name": "Alice", "age": 31, "role": "senior engineer"})),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT name, age, role, version, status FROM "doc_lcperson" WHERE document_id = $1',
                "DOC-LC-001",
            )
            assert row["age"] == 31
            assert row["role"] == "senior engineer"
            assert row["version"] == 2
            assert row["status"] == "active"
            # LATEST_ONLY: still exactly 1 row
            count = await conn.fetchval(
                'SELECT COUNT(*) FROM "doc_lcperson" WHERE document_id = $1', "DOC-LC-001"
            )
            assert count == 1

        # --- Step 3: Archive ---
        await _publish_and_process(worker, js, sub, "wip.documents", [
            make_event("document.archived", "document",
                       _doc_payload("DOC-LC-001", 2, "archived", {"name": "Alice", "age": 31, "role": "senior engineer"})),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT status FROM "doc_lcperson" WHERE document_id = $1', "DOC-LC-001"
            )
            assert row["status"] == "archived"

        # --- Step 4: Delete (soft) ---
        await _publish_and_process(worker, js, sub, "wip.documents", [
            make_event("document.deleted", "document",
                       _doc_payload("DOC-LC-001", 2, "deleted", {"name": "Alice", "age": 31, "role": "senior engineer"})),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT status FROM "doc_lcperson" WHERE document_id = $1', "DOC-LC-001"
            )
            assert row["status"] == "deleted"

    async def test_archive_without_prior_delete(self, pg_pool, nats_client):
        """Archive is a distinct status — not the same as delete."""
        worker, js, sub = await _make_worker(
            pg_pool, nats_client,
            template_cache={"TPL-LC-001": LIFECYCLE_TEMPLATE},
        )

        await _publish_and_process(worker, js, sub, "wip.documents", [
            make_event("document.created", "document",
                       _doc_payload("DOC-LC-002", 1, "active", {"name": "Bob"})),
            make_event("document.archived", "document",
                       _doc_payload("DOC-LC-002", 1, "archived", {"name": "Bob"})),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT status FROM "doc_lcperson" WHERE document_id = $1', "DOC-LC-002"
            )
            assert row["status"] == "archived"


# =============================================================================
# Terminology lifecycle: create → update → soft-delete → restore
# =============================================================================


def _terminology_payload(tid, status="active", **overrides):
    base = {
        "terminology_id": tid,
        "namespace": "test",
        "value": "LifecycleVocab",
        "label": "Lifecycle Vocab",
        "description": "Test vocabulary",
        "case_sensitive": False,
        "allow_multiple": False,
        "extensible": True,
        "mutable": False,
        "status": status,
        "term_count": 0,
        "created_at": "2026-01-20T10:00:00Z",
        "created_by": "test-user",
        "updated_at": None,
        "updated_by": None,
    }
    base.update(overrides)
    return base



@requires_e2e
class TestTerminologyLifecycle:
    """Terminology: create → update → soft-delete → restore."""

    async def test_full_lifecycle(self, pg_pool, nats_client):
        worker, js, sub = await _make_worker(pg_pool, nats_client)

        # --- Step 1: Create ---
        await _publish_and_process(worker, js, sub, "wip.terminologies", [
            make_event("terminology.created", "terminology",
                       _terminology_payload("VOCAB-LC-001")),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT * FROM terminologies WHERE terminology_id = $1 AND namespace = $2',
                "VOCAB-LC-001", "test",
            )
            assert row is not None
            assert row["value"] == "LifecycleVocab"
            assert row["status"] == "active"

        # --- Step 2: Update ---
        await _publish_and_process(worker, js, sub, "wip.terminologies", [
            make_event("terminology.updated", "terminology",
                       _terminology_payload("VOCAB-LC-001", value="LifecycleVocabRenamed",
                                            label="Renamed", term_count=5,
                                            updated_at="2026-01-20T12:00:00Z", updated_by="admin")),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT value, label, term_count, status FROM terminologies '
                'WHERE terminology_id = $1 AND namespace = $2',
                "VOCAB-LC-001", "test",
            )
            assert row["value"] == "LifecycleVocabRenamed"
            assert row["label"] == "Renamed"
            assert row["term_count"] == 5
            assert row["status"] == "active"

        # --- Step 3: Soft-delete ---
        await _publish_and_process(worker, js, sub, "wip.terminologies", [
            make_event_with_extras("terminology.deleted", "terminology",
                                   _terminology_payload("VOCAB-LC-001", status="inactive"),
                                   changed_by="admin"),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT status FROM terminologies WHERE terminology_id = $1 AND namespace = $2',
                "VOCAB-LC-001", "test",
            )
            assert row["status"] == "inactive"

        # --- Step 4: Restore ---
        await _publish_and_process(worker, js, sub, "wip.terminologies", [
            make_event("terminology.restored", "terminology",
                       _terminology_payload("VOCAB-LC-001", status="active",
                                            updated_at="2026-01-20T14:00:00Z", updated_by="admin")),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT status FROM terminologies WHERE terminology_id = $1 AND namespace = $2',
                "VOCAB-LC-001", "test",
            )
            assert row["status"] == "active"


# =============================================================================
# Term lifecycle: create → update → deprecate → hard-delete (mutable)
# =============================================================================


def _term_payload(term_id, status="active", **overrides):
    base = {
        "term_id": term_id,
        "namespace": "test",
        "terminology_id": "VOCAB-LC-001",
        "terminology_value": "LifecycleVocab",
        "value": "TermValue",
        "aliases": ["alias1"],
        "label": "Term Label",
        "sort_order": 0,
        "status": status,
        "created_at": "2026-01-20T10:00:00Z",
        "created_by": "test-user",
        "updated_at": None,
        "updated_by": None,
    }
    base.update(overrides)
    return base


@requires_e2e
class TestTermLifecycle:
    """Term: create → update → deprecate → hard-delete."""

    async def test_full_lifecycle(self, pg_pool, nats_client):
        worker, js, sub = await _make_worker(pg_pool, nats_client)

        # --- Step 1: Create ---
        await _publish_and_process(worker, js, sub, "wip.terms", [
            make_event("term.created", "term",
                       _term_payload("TERM-LC-001", value="Female", aliases=["F", "Fem"])),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT * FROM terms WHERE term_id = $1 AND namespace = $2',
                "TERM-LC-001", "test",
            )
            assert row is not None
            assert row["value"] == "Female"
            assert row["status"] == "active"

        # --- Step 2: Update ---
        await _publish_and_process(worker, js, sub, "wip.terms", [
            make_event("term.updated", "term",
                       _term_payload("TERM-LC-001", value="Female",
                                     label="Updated Label", sort_order=2,
                                     aliases=["F", "Fem", "W"],
                                     updated_at="2026-01-20T12:00:00Z", updated_by="admin")),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT label, sort_order, aliases, status FROM terms '
                'WHERE term_id = $1 AND namespace = $2',
                "TERM-LC-001", "test",
            )
            assert row["label"] == "Updated Label"
            assert row["sort_order"] == 2
            assert row["status"] == "active"

        # --- Step 3: Deprecate ---
        await _publish_and_process(worker, js, sub, "wip.terms", [
            make_event_with_extras("term.deprecated", "term", {
                "term_id": "TERM-LC-001",
                "namespace": "test",
                "deprecated_reason": "Replaced by inclusive term",
                "replaced_by_term_id": "TERM-LC-NEW",
            }, changed_by="admin"),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT status, deprecated_reason, replaced_by_term_id FROM terms '
                'WHERE term_id = $1 AND namespace = $2',
                "TERM-LC-001", "test",
            )
            assert row["status"] == "deprecated"
            assert row["deprecated_reason"] == "Replaced by inclusive term"
            assert row["replaced_by_term_id"] == "TERM-LC-NEW"

        # --- Step 4: Hard-delete (mutable terminology) ---
        await _publish_and_process(worker, js, sub, "wip.terms", [
            make_event("term.deleted", "term", {
                "term_id": "TERM-LC-001",
                "namespace": "test",
                "hard_delete": True,
            }),
        ])

        async with pg_pool.acquire() as conn:
            count = await conn.fetchval(
                'SELECT COUNT(*) FROM terms WHERE term_id = $1 AND namespace = $2',
                "TERM-LC-001", "test",
            )
            assert count == 0

    async def test_soft_delete(self, pg_pool, nats_client):
        """Soft-delete sets status to inactive (non-mutable terminology)."""
        worker, js, sub = await _make_worker(pg_pool, nats_client)

        await _publish_and_process(worker, js, sub, "wip.terms", [
            make_event("term.created", "term",
                       _term_payload("TERM-LC-SD", value="SoftDeleteMe")),
            make_event_with_extras("term.deleted", "term", {
                "term_id": "TERM-LC-SD",
                "namespace": "test",
            }, changed_by="admin"),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT status FROM terms WHERE term_id = $1 AND namespace = $2',
                "TERM-LC-SD", "test",
            )
            assert row["status"] == "inactive"


# =============================================================================
# Template lifecycle: create → update → activate → deactivate
# =============================================================================


def _template_payload(tpl_id, version=1, status="active", **overrides):
    base = {
        "template_id": tpl_id,
        "value": "LCTemplate",
        "version": version,
        "status": status,
        "namespace": "test",
        "label": "Lifecycle Template",
        "description": "Test template",
        "fields": [
            {"name": "title", "type": "string"},
            {"name": "count", "type": "integer"},
        ],
        "created_at": "2026-01-20T10:00:00Z",
        "created_by": "test-user",
        "updated_at": None,
        "updated_by": None,
    }
    base.update(overrides)
    return base


@requires_e2e
class TestTemplateLifecycle:
    """Template: create → update → deactivate, verifying metadata + doc table."""

    async def test_full_lifecycle(self, pg_pool, nats_client):
        worker, js, sub = await _make_worker(pg_pool, nats_client)

        # --- Step 1: Create ---
        await _publish_and_process(worker, js, sub, "wip.templates", [
            make_event("template.created", "template",
                       _template_payload("TPL-LC-010")),
        ])

        async with pg_pool.acquire() as conn:
            # Metadata row exists
            row = await conn.fetchrow(
                'SELECT * FROM templates WHERE template_id = $1 AND namespace = $2',
                "TPL-LC-010", "test",
            )
            assert row is not None
            assert row["value"] == "LCTemplate"
            assert row["status"] == "active"
            assert row["version"] == 1

            # Doc table created
            exists = await conn.fetchval(
                """SELECT EXISTS (SELECT FROM information_schema.tables
                   WHERE table_name = 'doc_lctemplate')"""
            )
            assert exists

        # --- Step 2: Update (new version, add field) ---
        await _publish_and_process(worker, js, sub, "wip.templates", [
            make_event("template.updated", "template",
                       _template_payload("TPL-LC-010", version=2,
                                         label="Updated Template",
                                         fields=[
                                             {"name": "title", "type": "string"},
                                             {"name": "count", "type": "integer"},
                                             {"name": "notes", "type": "string"},
                                         ],
                                         updated_at="2026-01-20T12:00:00Z",
                                         updated_by="admin")),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT version, label, status FROM templates '
                'WHERE template_id = $1 AND namespace = $2',
                "TPL-LC-010", "test",
            )
            assert row["version"] == 2
            assert row["label"] == "Updated Template"
            assert row["status"] == "active"

            # New column should exist in doc table
            col = await conn.fetchval(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_name = 'doc_lctemplate' AND column_name = 'notes'"""
            )
            assert col == "notes"

        # --- Step 3: Deactivate (template.deleted) ---
        await _publish_and_process(worker, js, sub, "wip.templates", [
            make_event_with_extras("template.deleted", "template",
                                   _template_payload("TPL-LC-010", version=2, status="deleted"),
                                   changed_by="admin"),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT status FROM templates WHERE template_id = $1 AND namespace = $2',
                "TPL-LC-010", "test",
            )
            assert row["status"] == "inactive"

            # Doc table should still exist (data preserved)
            exists = await conn.fetchval(
                """SELECT EXISTS (SELECT FROM information_schema.tables
                   WHERE table_name = 'doc_lctemplate')"""
            )
            assert exists


# =============================================================================
# Relationship lifecycle: create → soft-delete → recreate → hard-delete
# =============================================================================


@requires_e2e
class TestRelationshipLifecycle:
    """Relationship: create → soft-delete → recreate → hard-delete."""

    async def test_full_lifecycle(self, pg_pool, nats_client):
        worker, js, sub = await _make_worker(pg_pool, nats_client)

        rel_payload = {
            "namespace": "test",
            "source_term_id": "TERM-LC-A",
            "target_term_id": "TERM-LC-B",
            "relationship_type": "is_a",
            "source_term_value": "Cat",
            "target_term_value": "Animal",
            "source_terminology_id": "VOCAB-1",
            "target_terminology_id": "VOCAB-1",
            "metadata": {},
            "status": "active",
            "created_by": "test-user",
        }

        # --- Step 1: Create ---
        await _publish_and_process(worker, js, sub, "wip.relationships", [
            make_event("relationship.created", "relationship", rel_payload),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM term_relationships
                   WHERE source_term_id = $1 AND target_term_id = $2 AND namespace = $3""",
                "TERM-LC-A", "TERM-LC-B", "test",
            )
            assert row is not None
            assert row["relationship_type"] == "is_a"
            assert row["status"] == "active"

        # --- Step 2: Soft-delete ---
        await _publish_and_process(worker, js, sub, "wip.relationships", [
            make_event("relationship.deleted", "relationship", {
                "namespace": "test",
                "source_term_id": "TERM-LC-A",
                "target_term_id": "TERM-LC-B",
                "relationship_type": "is_a",
            }),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT status FROM term_relationships
                   WHERE source_term_id = $1 AND target_term_id = $2 AND namespace = $3""",
                "TERM-LC-A", "TERM-LC-B", "test",
            )
            assert row["status"] == "inactive"

        # --- Step 3: Recreate (upsert back to active) ---
        await _publish_and_process(worker, js, sub, "wip.relationships", [
            make_event("relationship.created", "relationship", rel_payload),
        ])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT status FROM term_relationships
                   WHERE source_term_id = $1 AND target_term_id = $2 AND namespace = $3""",
                "TERM-LC-A", "TERM-LC-B", "test",
            )
            assert row["status"] == "active"

        # --- Step 4: Hard-delete ---
        await _publish_and_process(worker, js, sub, "wip.relationships", [
            make_event("relationship.deleted", "relationship", {
                "namespace": "test",
                "source_term_id": "TERM-LC-A",
                "target_term_id": "TERM-LC-B",
                "relationship_type": "is_a",
                "hard_delete": True,
            }),
        ])

        async with pg_pool.acquire() as conn:
            count = await conn.fetchval(
                """SELECT COUNT(*) FROM term_relationships
                   WHERE source_term_id = $1 AND target_term_id = $2 AND namespace = $3""",
                "TERM-LC-A", "TERM-LC-B", "test",
            )
            assert count == 0


# =============================================================================
# Cross-entity: document depends on template status in PG
# =============================================================================


@requires_e2e
class TestCrossEntityConsistency:
    """Verify that template deactivation doesn't destroy document data."""

    async def test_documents_survive_template_deactivation(self, pg_pool, nats_client):
        """Documents remain queryable after their template is deactivated."""
        worker, js, sub = await _make_worker(pg_pool, nats_client)

        template = {
            "template_id": "TPL-LC-CROSS",
            "value": "LCCrossTest",
            "version": 1,
            "status": "active",
            "namespace": "test",
            "fields": [{"name": "note", "type": "string"}],
            "created_at": "2026-01-20T10:00:00Z",
            "created_by": "test-user",
        }

        # Create template
        await _publish_and_process(worker, js, sub, "wip.templates", [
            make_event("template.created", "template", template),
        ])

        # Create a document against it
        worker._template_cache["TPL-LC-CROSS"] = template
        await _publish_and_process(worker, js, sub, "wip.documents", [
            make_event("document.created", "document", {
                "document_id": "DOC-LC-CROSS",
                "template_id": "TPL-LC-CROSS",
                "namespace": "test",
                "template_version": 1,
                "version": 1,
                "status": "active",
                "identity_hash": "hash-cross",
                "data": {"note": "important data"},
                "term_references": [],
                "file_references": [],
                "created_at": "2026-01-20T10:00:00Z",
                "created_by": "test-user",
                "updated_at": None,
                "updated_by": None,
            }),
        ])

        # Deactivate the template
        await _publish_and_process(worker, js, sub, "wip.templates", [
            make_event_with_extras("template.deleted", "template",
                                   {**template, "status": "deleted"},
                                   changed_by="admin"),
        ])

        # Template marked inactive, but document data still there
        async with pg_pool.acquire() as conn:
            tpl = await conn.fetchrow(
                'SELECT status FROM templates WHERE template_id = $1',
                "TPL-LC-CROSS",
            )
            assert tpl["status"] == "inactive"

            doc = await conn.fetchrow(
                'SELECT note, status FROM "doc_lccrosstest" WHERE document_id = $1',
                "DOC-LC-CROSS",
            )
            assert doc is not None
            assert doc["note"] == "important data"
            assert doc["status"] == "active"
