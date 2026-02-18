"""
Tests for the SyncWorker.

All external dependencies (NATS, PostgreSQL, httpx) are mocked so these
tests can run without infrastructure.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reporting_sync.models import SyncStatus
from reporting_sync.worker import SyncWorker


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def sync_status():
    """Fresh SyncStatus for each test."""
    return SyncStatus(
        running=False,
        connected_to_nats=True,
        connected_to_postgres=True,
    )


@pytest.fixture
def mock_nats():
    """Mock NATS client."""
    return MagicMock()


@pytest.fixture
def mock_jetstream():
    """Mock JetStream context."""
    js = MagicMock()
    js.pull_subscribe = AsyncMock()
    return js


@pytest.fixture
def mock_pool():
    """Mock asyncpg connection pool with context manager support."""
    pool = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])

    # pool.acquire() returns an async context manager
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm

    return pool, conn


def _make_template(
    template_id="TPL-000001",
    value="person",
    version=1,
    fields=None,
    reporting=None,
):
    """Build a minimal template dict for tests."""
    return {
        "template_id": template_id,
        "value": value,
        "version": version,
        "fields": fields or [
            {"name": "first_name", "type": "string"},
            {"name": "last_name", "type": "string"},
            {"name": "email", "type": "string"},
        ],
        "reporting": reporting or {},
    }


def _make_document_event(
    event_type="document.created",
    document_id="DOC-000001",
    template_id="TPL-000001",
    data=None,
):
    """Build a document event payload."""
    return {
        "event_type": event_type,
        "document": {
            "document_id": document_id,
            "template_id": template_id,
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc123",
            "namespace": "wip",
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "test-user",
            "data": data or {
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
            },
            "term_references": [],
            "file_references": [],
        },
    }


def _make_template_event(
    event_type="template.created",
    template_id="TPL-000001",
    value="person",
):
    """Build a template event payload."""
    return {
        "event_type": event_type,
        "template": _make_template(template_id=template_id, value=value),
    }


def _make_nats_message(event_data: dict) -> MagicMock:
    """Create a mock NATS message with JSON data."""
    msg = MagicMock()
    msg.data = json.dumps(event_data).encode()
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    return msg


@pytest.fixture
def worker(mock_nats, mock_jetstream, mock_pool, sync_status):
    """Create a SyncWorker with all mocked dependencies."""
    pool, conn = mock_pool
    w = SyncWorker(mock_nats, mock_jetstream, pool, sync_status)
    # Mock the schema_manager methods
    w.schema_manager.table_exists = AsyncMock(return_value=False)
    w.schema_manager.create_table = AsyncMock(return_value="CREATE TABLE ...")
    w.schema_manager.ensure_table_for_template = AsyncMock(return_value="doc_person")
    w.schema_manager.update_table_schema = AsyncMock(return_value=[])
    return w


# =========================================================================
# Process Document Create Event
# =========================================================================


class TestProcessDocumentCreate:
    """Tests for processing document.created events."""

    @pytest.mark.asyncio
    async def test_create_event_fetches_template_and_inserts(self, worker, mock_pool):
        """A document.created event fetches the template, ensures the table, and inserts."""
        pool, conn = mock_pool
        template = _make_template()
        event = _make_document_event(event_type="document.created")

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=template)):
            result = await worker._process_document_event(event)

        assert result is True
        worker.schema_manager.ensure_table_for_template.assert_awaited_once()
        conn.execute.assert_awaited()

        # Verify SQL is an INSERT statement
        sql_arg = conn.execute.call_args[0][0]
        assert "INSERT INTO" in sql_arg

    @pytest.mark.asyncio
    async def test_create_event_message_ack(self, worker, mock_pool):
        """Full message processing: success results in ack."""
        pool, conn = mock_pool
        template = _make_template()
        event = _make_document_event()
        msg = _make_nats_message(event)

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=template)):
            await worker._process_message(msg)

        msg.ack.assert_awaited_once()
        assert worker.status.events_processed == 1


# =========================================================================
# Process Document Update Event
# =========================================================================


class TestProcessDocumentUpdate:
    """Tests for processing document.updated events."""

    @pytest.mark.asyncio
    async def test_update_event_upserts_row(self, worker, mock_pool):
        """A document.updated event produces an UPSERT SQL statement."""
        pool, conn = mock_pool
        template = _make_template()
        event = _make_document_event(event_type="document.updated")
        event["document"]["version"] = 2

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=template)):
            result = await worker._process_document_event(event)

        assert result is True
        sql_arg = conn.execute.call_args[0][0]
        assert "INSERT INTO" in sql_arg
        assert "ON CONFLICT" in sql_arg

    @pytest.mark.asyncio
    async def test_update_uses_latest_only_strategy_by_default(self, worker, mock_pool):
        """Default strategy is latest_only, which produces ON CONFLICT (document_id) DO UPDATE."""
        pool, conn = mock_pool
        template = _make_template()
        event = _make_document_event(event_type="document.updated")

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=template)):
            await worker._process_document_event(event)

        sql_arg = conn.execute.call_args[0][0]
        assert "ON CONFLICT (document_id)" in sql_arg
        assert "DO UPDATE SET" in sql_arg


# =========================================================================
# Process Document Delete Event
# =========================================================================


class TestProcessDocumentDelete:
    """Tests for processing document.deleted events."""

    @pytest.mark.asyncio
    async def test_delete_event_updates_status(self, worker, mock_pool):
        """A document.deleted event UPDATEs the row's status to 'deleted'."""
        pool, conn = mock_pool
        template = _make_template()
        event = _make_document_event(event_type="document.deleted")

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=template)):
            result = await worker._process_document_event(event)

        assert result is True
        # The delete path uses conn.execute with an UPDATE statement
        sql_arg = conn.execute.call_args[0][0]
        assert "UPDATE" in sql_arg
        assert "status" in sql_arg

    @pytest.mark.asyncio
    async def test_delete_passes_document_id_and_status(self, worker, mock_pool):
        """Delete SQL passes 'deleted' status and the document_id."""
        pool, conn = mock_pool
        template = _make_template()
        event = _make_document_event(
            event_type="document.deleted",
            document_id="DOC-999",
        )

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=template)):
            await worker._process_document_event(event)

        call_args = conn.execute.call_args
        # positional args: (sql, "deleted", "DOC-999")
        assert call_args[0][1] == "deleted"
        assert call_args[0][2] == "DOC-999"


# =========================================================================
# Process Template Event
# =========================================================================


class TestProcessTemplateEvent:
    """Tests for processing template events."""

    @pytest.mark.asyncio
    async def test_template_event_ensures_table(self, worker):
        """A template event triggers ensure_table_for_template."""
        event = _make_template_event()
        result = await worker._process_template_event(event)
        assert result is True
        worker.schema_manager.ensure_table_for_template.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_template_event_invalidates_cache(self, worker):
        """Template events clear the matching entry from the template cache."""
        template = _make_template(template_id="TPL-000001", value="person")
        worker._template_cache["TPL-000001"] = template

        event = _make_template_event(template_id="TPL-000001", value="person")
        await worker._process_template_event(event)

        assert "TPL-000001" not in worker._template_cache

    @pytest.mark.asyncio
    async def test_template_event_missing_value_returns_false(self, worker):
        """Template event without a 'value' field returns False."""
        event = {"event_type": "template.created", "template": {"template_id": "TPL-001"}}
        result = await worker._process_template_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_template_event_sync_disabled_skips_table(self, worker):
        """When sync is disabled for the template, ensure_table is still called
        but should be skipped by the method because config says so."""
        event = _make_template_event()
        event["template"]["reporting"] = {"sync_enabled": False}
        result = await worker._process_template_event(event)
        assert result is True
        # ensure_table_for_template should NOT be called when sync is disabled
        worker.schema_manager.ensure_table_for_template.assert_not_awaited()


# =========================================================================
# Template Caching
# =========================================================================


class TestTemplateCaching:
    """Tests for template fetch caching."""

    @pytest.mark.asyncio
    async def test_cache_hit_avoids_http_call(self, worker):
        """When template is cached, no HTTP request is made."""
        template = _make_template()
        worker._template_cache["TPL-000001"] = template

        with patch("reporting_sync.worker.httpx.AsyncClient") as mock_client_cls:
            result = await worker._fetch_template("TPL-000001")

        assert result == template
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_fetches_via_http(self, worker):
        """When template is not cached, an HTTP GET is made."""
        template = _make_template()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = template

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("reporting_sync.worker.httpx.AsyncClient", return_value=mock_client):
            result = await worker._fetch_template("TPL-000001")

        assert result == template
        assert "TPL-000001" in worker._template_cache

    @pytest.mark.asyncio
    async def test_second_fetch_uses_cache(self, worker, mock_pool):
        """After first fetch populates cache, second fetch uses it."""
        pool, conn = mock_pool
        template = _make_template()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = template

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("reporting_sync.worker.httpx.AsyncClient", return_value=mock_client):
            # First call fetches via HTTP
            result1 = await worker._fetch_template("TPL-000001")
            # Second call should use cache
            result2 = await worker._fetch_template("TPL-000001")

        assert result1 == result2
        # HTTP client.get should only be called once
        assert mock_client.get.await_count == 1


# =========================================================================
# Error Handling
# =========================================================================


class TestErrorHandling:
    """Tests for error handling in event processing."""

    @pytest.mark.asyncio
    async def test_invalid_event_data_missing_document_id(self, worker):
        """Event without document_id returns False."""
        event = {
            "event_type": "document.created",
            "document": {
                "template_id": "TPL-000001",
                # missing document_id
            },
        }
        result = await worker._process_document_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_invalid_event_data_missing_template_id(self, worker):
        """Event without template_id returns False."""
        event = {
            "event_type": "document.created",
            "document": {
                "document_id": "DOC-000001",
                # missing template_id
            },
        }
        result = await worker._process_document_event(event)
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_template_returns_false(self, worker):
        """When template cannot be fetched, returns False."""
        event = _make_document_event()

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=None)):
            result = await worker._process_document_event(event)

        assert result is False

    @pytest.mark.asyncio
    async def test_template_fetch_404(self, worker):
        """HTTP 404 for template returns None."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("reporting_sync.worker.httpx.AsyncClient", return_value=mock_client):
            result = await worker._fetch_template("TPL-NOTFOUND")

        assert result is None
        assert "TPL-NOTFOUND" not in worker._template_cache

    @pytest.mark.asyncio
    async def test_template_fetch_network_error(self, worker):
        """Network error during template fetch returns None."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("reporting_sync.worker.httpx.AsyncClient", return_value=mock_client):
            result = await worker._fetch_template("TPL-000001")

        assert result is None

    @pytest.mark.asyncio
    async def test_db_insert_error_raises(self, worker, mock_pool):
        """Database error during insert propagates as exception."""
        pool, conn = mock_pool
        conn.execute = AsyncMock(side_effect=Exception("unique constraint violation"))
        template = _make_template()
        event = _make_document_event()

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=template)):
            with pytest.raises(Exception, match="unique constraint violation"):
                await worker._process_document_event(event)


# =========================================================================
# Message Ack / Nak
# =========================================================================


class TestMessageAckNak:
    """Tests for message acknowledgement behavior."""

    @pytest.mark.asyncio
    async def test_successful_event_acks_message(self, worker, mock_pool):
        """Successful event processing acks the NATS message."""
        pool, conn = mock_pool
        template = _make_template()
        event = _make_document_event()
        msg = _make_nats_message(event)

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=template)):
            await worker._process_message(msg)

        msg.ack.assert_awaited_once()
        msg.nak.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_event_naks_message(self, worker, mock_pool):
        """Failed event processing naks the NATS message for retry."""
        pool, conn = mock_pool
        event = _make_document_event()
        msg = _make_nats_message(event)

        # Template not found = returns False = nak
        with patch.object(worker, "_fetch_template", AsyncMock(return_value=None)):
            await worker._process_message(msg)

        msg.nak.assert_awaited_once()
        msg.ack.assert_not_awaited()
        assert worker.status.events_failed == 1

    @pytest.mark.asyncio
    async def test_invalid_json_acks_message(self, worker):
        """Invalid JSON message is acked (not retried) to avoid infinite loops."""
        msg = MagicMock()
        msg.data = b"not valid json {"
        msg.ack = AsyncMock()
        msg.nak = AsyncMock()

        await worker._process_message(msg)

        msg.ack.assert_awaited_once()
        msg.nak.assert_not_awaited()
        assert worker.status.events_failed == 1

    @pytest.mark.asyncio
    async def test_exception_during_processing_naks_message(self, worker, mock_pool):
        """Unhandled exception during processing naks the message."""
        pool, conn = mock_pool
        conn.execute = AsyncMock(side_effect=RuntimeError("db connection lost"))
        template = _make_template()
        event = _make_document_event()
        msg = _make_nats_message(event)

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=template)):
            await worker._process_message(msg)

        msg.nak.assert_awaited_once()
        assert worker.status.events_failed == 1

    @pytest.mark.asyncio
    async def test_unknown_event_type_acks(self, worker):
        """Unknown event types are acked (not retried)."""
        event = {"event_type": "something.unknown", "data": {}}
        msg = _make_nats_message(event)

        await worker._process_message(msg)

        msg.ack.assert_awaited_once()
        assert worker.status.events_processed == 1


# =========================================================================
# Sync Disabled
# =========================================================================


class TestSyncDisabled:
    """Tests for events where sync is disabled on the template."""

    @pytest.mark.asyncio
    async def test_sync_disabled_skips_and_returns_true(self, worker, mock_pool):
        """When template has sync_enabled=False, event is skipped (not an error)."""
        pool, conn = mock_pool
        template = _make_template(reporting={"sync_enabled": False})
        event = _make_document_event()

        with patch.object(worker, "_fetch_template", AsyncMock(return_value=template)):
            result = await worker._process_document_event(event)

        assert result is True
        conn.execute.assert_not_awaited()


# =========================================================================
# Worker Start/Stop
# =========================================================================


class TestWorkerStartStop:
    """Tests for worker lifecycle."""

    @pytest.mark.asyncio
    async def test_stop_sets_running_false(self, worker):
        await worker.stop()
        assert worker._running is False
        assert worker.status.running is False
