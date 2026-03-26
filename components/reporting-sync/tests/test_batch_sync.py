"""
Tests for BatchSyncService — terminology, term, and relationship batch sync.

Covers the Def-Store API → PostgreSQL batch sync path.
All external dependencies (httpx, asyncpg) are mocked.
"""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reporting_sync.batch_sync import BatchSyncService


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
def service(mock_pool):
    """BatchSyncService with mocked pool and schema_manager."""
    pool, conn = mock_pool
    svc = BatchSyncService(pool)
    svc.schema_manager.ensure_terminologies_table = AsyncMock(return_value="terminologies")
    svc.schema_manager.ensure_terms_table = AsyncMock(return_value="terms")
    svc.schema_manager.ensure_term_relationships_table = AsyncMock(return_value="term_relationships")
    return svc


def _make_api_response(items, page=1, pages=1, status_code=200):
    """Create a mock httpx response with paginated items."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"items": items, "page": page, "pages": pages}
    return resp


# =========================================================================
# batch_sync_terminologies
# =========================================================================


SAMPLE_TERMINOLOGY = {
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
    "updated_at": "2024-02-15T14:30:00Z",
    "updated_by": "editor",
}


class TestBatchSyncTerminologies:
    """Tests for batch_sync_terminologies."""

    @pytest.mark.asyncio
    async def test_syncs_one_page(self, service, mock_pool):
        """Single page of terminologies is synced."""
        pool, conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_TERMINOLOGY]))
            mock_client_cls.return_value = mock_client

            result = await service.batch_sync_terminologies()

        assert result["synced"] == 1
        assert result["failed"] == 0
        conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_datetime_fields_are_parsed(self, service, mock_pool):
        """created_at and updated_at are datetime objects, not strings."""
        pool, conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_TERMINOLOGY]))
            mock_client_cls.return_value = mock_client

            await service.batch_sync_terminologies()

        args = conn.execute.call_args[0]
        # args[0] is SQL, args[1..14] are positional values
        # $11 = created_at (index 11), $13 = updated_at (index 13)
        created_at = args[11]
        updated_at = args[13]
        assert isinstance(created_at, datetime), f"created_at should be datetime, got {type(created_at)}"
        assert isinstance(updated_at, datetime), f"updated_at should be datetime, got {type(updated_at)}"

    @pytest.mark.asyncio
    async def test_boolean_fields_are_correct_type(self, service, mock_pool):
        """case_sensitive, allow_multiple, extensible are booleans."""
        pool, conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_TERMINOLOGY]))
            mock_client_cls.return_value = mock_client

            await service.batch_sync_terminologies()

        args = conn.execute.call_args[0]
        # $6 = case_sensitive (index 6), $7 = allow_multiple (7), $8 = extensible (8)
        assert isinstance(args[6], bool), f"case_sensitive should be bool, got {type(args[6])}"
        assert isinstance(args[7], bool), f"allow_multiple should be bool, got {type(args[7])}"
        assert isinstance(args[8], bool), f"extensible should be bool, got {type(args[8])}"

    @pytest.mark.asyncio
    async def test_term_count_is_int(self, service, mock_pool):
        """term_count is an integer."""
        pool, conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_TERMINOLOGY]))
            mock_client_cls.return_value = mock_client

            await service.batch_sync_terminologies()

        args = conn.execute.call_args[0]
        # $10 = term_count (index 10)
        assert isinstance(args[10], int), f"term_count should be int, got {type(args[10])}"

    @pytest.mark.asyncio
    async def test_none_datetime_handled(self, service, mock_pool):
        """None datetime values are passed as None, not causing errors."""
        pool, conn = mock_pool

        terminology = {**SAMPLE_TERMINOLOGY, "created_at": None, "updated_at": None}

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([terminology]))
            mock_client_cls.return_value = mock_client

            result = await service.batch_sync_terminologies()

        assert result["synced"] == 1
        args = conn.execute.call_args[0]
        assert args[11] is None  # created_at
        assert args[13] is None  # updated_at


# =========================================================================
# batch_sync_terms
# =========================================================================


SAMPLE_TERM = {
    "term_id": "T-001",
    "namespace": "wip",
    "terminology_id": "TRM-001",
    "terminology_value": "COUNTRIES",
    "value": "United Kingdom",
    "aliases": ["UK", "GB"],
    "label": "United Kingdom",
    "description": "Country in Europe",
    "sort_order": 5,
    "parent_term_id": None,
    "status": "active",
    "deprecated_reason": None,
    "replaced_by_term_id": None,
    "created_at": "2024-01-30T10:00:00Z",
    "created_by": "admin",
    "updated_at": "2024-02-15T14:30:00Z",
    "updated_by": "editor",
}


class TestBatchSyncTerms:
    """Tests for batch_sync_terms."""

    def _mock_client(self, terminologies, terms_per_terminology):
        """Create a mock httpx client that returns terminologies then terms."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        responses = [_make_api_response(terminologies)]
        for terms in terms_per_terminology:
            responses.append(_make_api_response(terms))

        mock_client.get = AsyncMock(side_effect=responses)
        return mock_client

    @pytest.mark.asyncio
    async def test_syncs_terms(self, service, mock_pool):
        """Terms are fetched per terminology and synced."""
        pool, conn = mock_pool

        terminologies = [{"terminology_id": "TRM-001"}]
        terms = [SAMPLE_TERM]

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value = self._mock_client(terminologies, [terms])

            result = await service.batch_sync_terms()

        assert result["synced"] == 1
        assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_datetime_fields_are_parsed(self, service, mock_pool):
        """created_at and updated_at are datetime objects."""
        pool, conn = mock_pool

        terminologies = [{"terminology_id": "TRM-001"}]

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value = self._mock_client(terminologies, [[SAMPLE_TERM]])

            await service.batch_sync_terms()

        args = conn.execute.call_args[0]
        # $14 = created_at (index 14), $16 = updated_at (index 16)
        created_at = args[14]
        updated_at = args[16]
        assert isinstance(created_at, datetime), f"created_at should be datetime, got {type(created_at)}"
        assert isinstance(updated_at, datetime), f"updated_at should be datetime, got {type(updated_at)}"

    @pytest.mark.asyncio
    async def test_aliases_serialized_as_json(self, service, mock_pool):
        """aliases list is JSON-serialized."""
        pool, conn = mock_pool

        terminologies = [{"terminology_id": "TRM-001"}]

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value = self._mock_client(terminologies, [[SAMPLE_TERM]])

            await service.batch_sync_terms()

        args = conn.execute.call_args[0]
        # $6 = aliases (index 6)
        assert args[6] == json.dumps(["UK", "GB"])

    @pytest.mark.asyncio
    async def test_sort_order_is_int(self, service, mock_pool):
        """sort_order is an integer."""
        pool, conn = mock_pool

        terminologies = [{"terminology_id": "TRM-001"}]

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value = self._mock_client(terminologies, [[SAMPLE_TERM]])

            await service.batch_sync_terms()

        args = conn.execute.call_args[0]
        # $9 = sort_order (index 9)
        assert isinstance(args[9], int), f"sort_order should be int, got {type(args[9])}"


# =========================================================================
# batch_sync_relationships
# =========================================================================


SAMPLE_RELATIONSHIP = {
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
    "created_at": "2024-01-30T10:00:00Z",
    "created_by": "admin",
}


class TestBatchSyncRelationships:
    """Tests for batch_sync_relationships."""

    @pytest.mark.asyncio
    async def test_syncs_relationships(self, service, mock_pool):
        """Relationships are fetched and synced."""
        pool, conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_RELATIONSHIP]))
            mock_client_cls.return_value = mock_client

            result = await service.batch_sync_relationships()

        assert result["synced"] == 1
        assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_created_at_is_parsed(self, service, mock_pool):
        """created_at is a datetime object, not a raw string."""
        pool, conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_RELATIONSHIP]))
            mock_client_cls.return_value = mock_client

            await service.batch_sync_relationships()

        args = conn.execute.call_args[0]
        # $11 = created_at (index 11)
        created_at = args[11]
        assert isinstance(created_at, datetime), f"created_at should be datetime, got {type(created_at)}"

    @pytest.mark.asyncio
    async def test_metadata_serialized_as_json(self, service, mock_pool):
        """metadata dict is JSON-serialized."""
        pool, conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_RELATIONSHIP]))
            mock_client_cls.return_value = mock_client

            await service.batch_sync_relationships()

        args = conn.execute.call_args[0]
        # $9 = metadata (index 9)
        assert args[9] == json.dumps({"source_ontology": "SNOMED"})

    @pytest.mark.asyncio
    async def test_api_error_returns_zero(self, service, mock_pool):
        """Non-200 API response results in zero synced."""
        pool, conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([], status_code=500))
            mock_client_cls.return_value = mock_client

            result = await service.batch_sync_relationships()

        assert result["synced"] == 0
        conn.execute.assert_not_awaited()
