"""Tests for hard-delete event handling in Reporting-Sync.

Unit tests (mocked NATS + PostgreSQL) verifying that hard_delete events
produce DELETE FROM statements instead of UPDATE status.

Covers all entity types: documents, templates, terminologies, terms, relationships.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reporting_sync.models import SyncStatus
from reporting_sync.worker import SyncWorker


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def sync_status():
    return SyncStatus(running=False, connected_to_nats=True, connected_to_postgres=True)


@pytest.fixture
def mock_nats():
    return MagicMock()


@pytest.fixture
def mock_jetstream():
    js = MagicMock()
    js.pull_subscribe = AsyncMock()
    return js


@pytest.fixture
def mock_pool():
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
def worker(mock_nats, mock_jetstream, mock_pool, sync_status):
    pool, _conn = mock_pool
    w = SyncWorker(mock_nats, mock_jetstream, pool, sync_status)
    w.schema_manager.table_exists = AsyncMock(return_value=False)
    w.schema_manager.create_table = AsyncMock(return_value="CREATE TABLE ...")
    w.schema_manager.ensure_table_for_template = AsyncMock(return_value="doc_person")
    w.schema_manager.ensure_templates_table = AsyncMock(return_value="templates")
    w.schema_manager.ensure_terminologies_table = AsyncMock(return_value="terminologies")
    w.schema_manager.ensure_terms_table = AsyncMock(return_value="terms")
    w.schema_manager.ensure_term_relationships_table = AsyncMock(return_value="term_relationships")
    w.schema_manager.update_table_schema = AsyncMock(return_value=[])
    return w


# =========================================================================
# Helpers
# =========================================================================


def _make_template(template_id="TPL-000001", value="person", version=1, namespace="wip"):
    return {
        "template_id": template_id,
        "value": value,
        "version": version,
        "namespace": namespace,
        "fields": [
            {"name": "first_name", "type": "string"},
            {"name": "last_name", "type": "string"},
        ],
        "reporting": {},
    }


# =========================================================================
# Document Hard-Delete
# =========================================================================


class TestDocumentHardDelete:
    """Tests for document.deleted events with hard_delete=True."""

    @pytest.mark.asyncio
    async def test_hard_delete_all_versions(self, worker, mock_pool):
        """hard_delete without version field produces DELETE FROM by document_id only."""
        _, conn = mock_pool
        template = _make_template()
        # Omit "version" from document payload to trigger all-version delete
        event = {
            "event_type": "document.deleted",
            "document": {
                "document_id": "DOC-001",
                "template_id": "TPL-000001",
                "template_version": 1,
                "status": "active",
                "identity_hash": "abc",
                "namespace": "wip",
                "data": {"first_name": "John", "last_name": "Doe"},
                "term_references": [],
                "file_references": [],
                "hard_delete": True,
            },
        }

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=template)):
            result = await worker._process_document_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "DELETE FROM" in sql
        assert "document_id" in sql
        # All-version: no version filter
        args = conn.execute.call_args[0]
        assert len(args) == 2  # (sql, document_id) — no version param

    @pytest.mark.asyncio
    async def test_hard_delete_specific_version(self, worker, mock_pool):
        """hard_delete with version produces DELETE FROM with version filter."""
        _, conn = mock_pool
        template = _make_template()
        event = {
            "event_type": "document.deleted",
            "document": {
                "document_id": "DOC-002",
                "template_id": "TPL-000001",
                "template_version": 1,
                "version": 2,
                "status": "active",
                "identity_hash": "abc",
                "namespace": "wip",
                "data": {"first_name": "Jane", "last_name": "Doe"},
                "term_references": [],
                "file_references": [],
                "hard_delete": True,
            },
        }

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=template)):
            result = await worker._process_document_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "DELETE FROM" in sql
        # Should have both document_id and version params
        args = conn.execute.call_args[0]
        assert args[1] == "DOC-002"
        assert args[2] == 2

    @pytest.mark.asyncio
    async def test_soft_delete_uses_update(self, worker, mock_pool):
        """Soft delete (no hard_delete flag) uses UPDATE, not DELETE."""
        _, conn = mock_pool
        template = _make_template()
        event = {
            "event_type": "document.deleted",
            "document": {
                "document_id": "DOC-003",
                "template_id": "TPL-000001",
                "template_version": 1,
                "version": 1,
                "status": "active",
                "identity_hash": "abc",
                "namespace": "wip",
                "data": {"first_name": "Soft", "last_name": "Delete"},
                "term_references": [],
                "file_references": [],
            },
        }

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=template)):
            result = await worker._process_document_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "status" in sql


# =========================================================================
# Template Hard-Delete
# =========================================================================


class TestTemplateHardDelete:
    """Tests for template.deleted events with hard_delete=True."""

    @pytest.mark.asyncio
    async def test_hard_delete_all_versions(self, worker, mock_pool):
        """hard_delete template without version produces DELETE FROM metadata table."""
        _, conn = mock_pool
        event = {
            "event_type": "template.deleted",
            "template": {
                "template_id": "TPL-HD-001",
                "value": "hard_test",
                "version": 1,
                "namespace": "wip",
                "fields": [],
                "reporting": {},
                "hard_delete": True,
            },
        }

        result = await worker._process_template_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "DELETE FROM" in sql
        assert "template_id" in sql

    @pytest.mark.asyncio
    async def test_hard_delete_specific_version(self, worker, mock_pool):
        """hard_delete template with version includes version in DELETE."""
        _, conn = mock_pool
        event = {
            "event_type": "template.deleted",
            "template": {
                "template_id": "TPL-HD-002",
                "value": "version_test",
                "version": 3,
                "namespace": "wip",
                "fields": [],
                "reporting": {},
                "hard_delete": True,
            },
        }

        result = await worker._process_template_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "DELETE FROM" in sql
        assert "version" in sql
        args = conn.execute.call_args[0]
        assert args[1] == "wip"
        assert args[2] == "TPL-HD-002"
        assert args[3] == 3

    @pytest.mark.asyncio
    async def test_soft_delete_uses_update(self, worker, mock_pool):
        """Soft delete template uses UPDATE to set inactive."""
        _, conn = mock_pool
        event = {
            "event_type": "template.deleted",
            "template": {
                "template_id": "TPL-SD-001",
                "value": "soft_test",
                "version": 1,
                "namespace": "wip",
                "fields": [],
                "reporting": {},
            },
        }

        result = await worker._process_template_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "'inactive'" in sql


# =========================================================================
# Terminology Hard-Delete
# =========================================================================


class TestTerminologyHardDelete:
    """Tests for terminology.deleted events with hard_delete=True."""

    @pytest.mark.asyncio
    async def test_hard_delete_produces_delete_sql(self, worker, mock_pool):
        """hard_delete terminology produces DELETE FROM terminologies."""
        _, conn = mock_pool
        event = {
            "event_type": "terminology.deleted",
            "terminology": {
                "terminology_id": "TERM-HD-001",
                "namespace": "wip",
                "value": "TEST_TERM",
                "label": "Test",
                "hard_delete": True,
            },
        }

        result = await worker._process_terminology_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "DELETE FROM" in sql
        assert "terminology_id" in sql
        args = conn.execute.call_args[0]
        assert args[1] == "wip"
        assert args[2] == "TERM-HD-001"

    @pytest.mark.asyncio
    async def test_soft_delete_uses_update(self, worker, mock_pool):
        """Soft delete terminology uses UPDATE."""
        _, conn = mock_pool
        event = {
            "event_type": "terminology.deleted",
            "terminology": {
                "terminology_id": "TERM-SD-001",
                "namespace": "wip",
                "value": "SOFT_TERM",
                "label": "Soft",
            },
            "changed_by": "test-user",
        }

        result = await worker._process_terminology_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "'inactive'" in sql


# =========================================================================
# Term Hard-Delete
# =========================================================================


class TestTermHardDelete:
    """Tests for term.deleted events with hard_delete=True."""

    @pytest.mark.asyncio
    async def test_hard_delete_cascades_relationships(self, worker, mock_pool):
        """hard_delete term deletes from term_relationships and terms tables."""
        _, conn = mock_pool
        event = {
            "event_type": "term.deleted",
            "term": {
                "term_id": "T-HD-001",
                "namespace": "wip",
                "terminology_id": "TERM-001",
                "value": "REMOVED",
                "label": "Removed",
                "hard_delete": True,
            },
        }

        result = await worker._process_term_event(event)

        assert result is True
        # Should have two DELETE calls: relationships first, then term
        assert conn.execute.await_count >= 2
        calls = [c[0][0] for c in conn.execute.call_args_list]
        rel_delete = [c for c in calls if "term_relationships" in c and "DELETE" in c]
        term_delete = [c for c in calls if "terms" in c and "DELETE" in c and "term_relationships" not in c]
        assert len(rel_delete) == 1, f"Expected 1 relationship DELETE, got: {calls}"
        assert len(term_delete) == 1, f"Expected 1 term DELETE, got: {calls}"

    @pytest.mark.asyncio
    async def test_soft_delete_uses_update(self, worker, mock_pool):
        """Soft delete term uses UPDATE."""
        _, conn = mock_pool
        event = {
            "event_type": "term.deleted",
            "term": {
                "term_id": "T-SD-001",
                "namespace": "wip",
                "terminology_id": "TERM-001",
                "value": "SOFT",
                "label": "Soft",
            },
            "changed_by": "test-user",
        }

        result = await worker._process_term_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "'inactive'" in sql


# =========================================================================
# Relationship Hard-Delete
# =========================================================================


class TestRelationshipHardDelete:
    """Tests for relationship.deleted events with hard_delete=True."""

    @pytest.mark.asyncio
    async def test_hard_delete_produces_delete_sql(self, worker, mock_pool):
        """hard_delete relationship produces DELETE FROM term_relationships."""
        _, conn = mock_pool
        event = {
            "event_type": "relationship.deleted",
            "relationship": {
                "namespace": "wip",
                "source_term_id": "T-001",
                "target_term_id": "T-002",
                "relationship_type": "is_a",
                "source_terminology_id": "TERM-001",
                "target_terminology_id": "TERM-001",
                "hard_delete": True,
            },
        }

        result = await worker._process_relationship_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "DELETE FROM" in sql
        assert "source_term_id" in sql
        assert "target_term_id" in sql
        assert "relationship_type" in sql

    @pytest.mark.asyncio
    async def test_soft_delete_uses_update(self, worker, mock_pool):
        """Soft delete relationship uses UPDATE."""
        _, conn = mock_pool
        event = {
            "event_type": "relationship.deleted",
            "relationship": {
                "namespace": "wip",
                "source_term_id": "T-003",
                "target_term_id": "T-004",
                "relationship_type": "is_a",
                "source_terminology_id": "TERM-002",
                "target_terminology_id": "TERM-002",
            },
        }

        result = await worker._process_relationship_event(event)

        assert result is True
        sql = conn.execute.call_args[0][0]
        assert "UPDATE" in sql
        assert "'inactive'" in sql
