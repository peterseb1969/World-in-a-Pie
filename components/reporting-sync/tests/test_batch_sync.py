"""
Tests for BatchSyncService — terminology, term, and relation batch sync.

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
    pool, _conn = mock_pool
    svc = BatchSyncService(pool)
    svc.schema_manager.ensure_terminologies_table = AsyncMock(return_value="terminologies")
    svc.schema_manager.ensure_terms_table = AsyncMock(return_value="terms")
    svc.schema_manager.ensure_term_relations_table = AsyncMock(return_value="term_relations")
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
        _pool, conn = mock_pool

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
    async def test_namespace_none_omits_param(self, service, mock_pool):
        """When namespace=None, the API request omits the namespace parameter."""
        _pool, _conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_TERMINOLOGY]))
            mock_client_cls.return_value = mock_client

            await service.batch_sync_terminologies(namespace=None)

        # Check the params passed to the GET request
        call_args = mock_client.get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params", {})
        assert "namespace" not in params

    @pytest.mark.asyncio
    async def test_namespace_explicit_includes_param(self, service, mock_pool):
        """When namespace is set, the API request includes the namespace parameter."""
        _pool, _conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_TERMINOLOGY]))
            mock_client_cls.return_value = mock_client

            await service.batch_sync_terminologies(namespace="custom")

        call_args = mock_client.get.call_args
        params = call_args.kwargs.get("params") or call_args[1].get("params", {})
        assert params.get("namespace") == "custom"

    @pytest.mark.asyncio
    async def test_namespace_fallback_in_insert(self, service, mock_pool):
        """When item has no namespace, fallback to the namespace parameter."""
        _pool, conn = mock_pool

        no_ns_terminology = {**SAMPLE_TERMINOLOGY}
        del no_ns_terminology["namespace"]

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([no_ns_terminology]))
            mock_client_cls.return_value = mock_client

            await service.batch_sync_terminologies(namespace="wip")

        args = conn.execute.call_args[0]
        # $2 = namespace (index 2)
        assert args[2] == "wip"

    @pytest.mark.asyncio
    async def test_multi_page_sync(self, service, mock_pool):
        """Multiple pages are fetched and all terminologies synced."""
        _pool, _conn = mock_pool

        page1 = _make_api_response([SAMPLE_TERMINOLOGY], page=1, pages=2)
        term2 = {**SAMPLE_TERMINOLOGY, "terminology_id": "TRM-002", "value": "CITIES"}
        page2 = _make_api_response([term2], page=2, pages=2)

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=[page1, page2])
            mock_client_cls.return_value = mock_client

            result = await service.batch_sync_terminologies()

        assert result["synced"] == 2
        assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_datetime_fields_are_parsed(self, service, mock_pool):
        """created_at and updated_at are datetime objects, not strings."""
        _pool, conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_TERMINOLOGY]))
            mock_client_cls.return_value = mock_client

            await service.batch_sync_terminologies()

        args = conn.execute.call_args[0]
        # args[0] is SQL, args[1..15] are positional values
        # $12 = created_at (index 12), $14 = updated_at (index 14)
        created_at = args[12]
        updated_at = args[14]
        assert isinstance(created_at, datetime), f"created_at should be datetime, got {type(created_at)}"
        assert isinstance(updated_at, datetime), f"updated_at should be datetime, got {type(updated_at)}"

    @pytest.mark.asyncio
    async def test_boolean_fields_are_correct_type(self, service, mock_pool):
        """case_sensitive, allow_multiple, extensible are booleans."""
        _pool, conn = mock_pool

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
        _pool, conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_TERMINOLOGY]))
            mock_client_cls.return_value = mock_client

            await service.batch_sync_terminologies()

        args = conn.execute.call_args[0]
        # $11 = term_count (index 11)
        assert isinstance(args[11], int), f"term_count should be int, got {type(args[11])}"

    @pytest.mark.asyncio
    async def test_none_datetime_handled(self, service, mock_pool):
        """None datetime values are passed as None, not causing errors."""
        _pool, conn = mock_pool

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
        assert args[12] is None  # created_at
        assert args[14] is None  # updated_at


# =========================================================================
# batch_sync_terms
# =========================================================================


SAMPLE_TERM = {
    "term_id": "0190b000-0000-7000-0000-000000000001",
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
        _pool, _conn = mock_pool

        terminologies = [{"terminology_id": "TRM-001"}]
        terms = [SAMPLE_TERM]

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value = self._mock_client(terminologies, [terms])

            result = await service.batch_sync_terms()

        assert result["synced"] == 1
        assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_namespace_none_omits_param(self, service, mock_pool):
        """When namespace=None, the terminology list request omits namespace."""
        _pool, _conn = mock_pool

        terminologies = [{"terminology_id": "TRM-001"}]

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = self._mock_client(terminologies, [[SAMPLE_TERM]])
            mock_client_cls.return_value = mock_client

            await service.batch_sync_terms(namespace=None)

        # First call is the terminology list
        first_call = mock_client.get.call_args_list[0]
        params = first_call.kwargs.get("params") or first_call[1].get("params", {})
        assert "namespace" not in params

    @pytest.mark.asyncio
    async def test_namespace_explicit_includes_param(self, service, mock_pool):
        """When namespace is set, the terminology list request includes it."""
        _pool, _conn = mock_pool

        terminologies = [{"terminology_id": "TRM-001"}]

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = self._mock_client(terminologies, [[SAMPLE_TERM]])
            mock_client_cls.return_value = mock_client

            await service.batch_sync_terms(namespace="custom")

        first_call = mock_client.get.call_args_list[0]
        params = first_call.kwargs.get("params") or first_call[1].get("params", {})
        assert params.get("namespace") == "custom"

    @pytest.mark.asyncio
    async def test_namespace_fallback_in_term_insert(self, service, mock_pool):
        """When term has no namespace, fallback to the namespace parameter."""
        _pool, conn = mock_pool

        no_ns_term = {**SAMPLE_TERM}
        del no_ns_term["namespace"]
        terminologies = [{"terminology_id": "TRM-001"}]

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value = self._mock_client(terminologies, [[no_ns_term]])

            await service.batch_sync_terms(namespace="wip")

        args = conn.execute.call_args[0]
        # $2 = namespace (index 2)
        assert args[2] == "wip"

    @pytest.mark.asyncio
    async def test_datetime_fields_are_parsed(self, service, mock_pool):
        """created_at and updated_at are datetime objects."""
        _pool, conn = mock_pool

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
        _pool, conn = mock_pool

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
        _pool, conn = mock_pool

        terminologies = [{"terminology_id": "TRM-001"}]

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value = self._mock_client(terminologies, [[SAMPLE_TERM]])

            await service.batch_sync_terms()

        args = conn.execute.call_args[0]
        # $9 = sort_order (index 9)
        assert isinstance(args[9], int), f"sort_order should be int, got {type(args[9])}"


# =========================================================================
# batch_sync_term_relations
# =========================================================================


SAMPLE_RELATIONSHIP = {
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
    "created_at": "2024-01-30T10:00:00Z",
    "created_by": "admin",
}


class TestBatchSyncRelations:
    """Tests for batch_sync_term_relations."""

    @pytest.mark.asyncio
    async def test_syncs_relations(self, service, mock_pool):
        """Relations are fetched and synced."""
        _pool, _conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_RELATIONSHIP]))
            mock_client_cls.return_value = mock_client

            result = await service.batch_sync_term_relations(namespace="wip")

        assert result["synced"] == 1
        assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_created_at_is_parsed(self, service, mock_pool):
        """created_at is a datetime object, not a raw string."""
        _pool, conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_RELATIONSHIP]))
            mock_client_cls.return_value = mock_client

            await service.batch_sync_term_relations(namespace="wip")

        args = conn.execute.call_args[0]
        # $11 = created_at (index 11)
        created_at = args[11]
        assert isinstance(created_at, datetime), f"created_at should be datetime, got {type(created_at)}"

    @pytest.mark.asyncio
    async def test_metadata_serialized_as_json(self, service, mock_pool):
        """metadata dict is JSON-serialized."""
        _pool, conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([SAMPLE_RELATIONSHIP]))
            mock_client_cls.return_value = mock_client

            await service.batch_sync_term_relations(namespace="wip")

        args = conn.execute.call_args[0]
        # $9 = metadata (index 9)
        assert args[9] == json.dumps({"source_ontology": "SNOMED"})

    @pytest.mark.asyncio
    async def test_api_error_returns_zero(self, service, mock_pool):
        """Non-200 API response results in zero synced."""
        _pool, conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([], status_code=500))
            mock_client_cls.return_value = mock_client

            result = await service.batch_sync_term_relations(namespace="wip")

        assert result["synced"] == 0
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_namespaceless_sync_routes_rows_per_namespace(self, service, mock_pool):
        """Without a namespace, relations from different namespaces land in
        their own namespace's table and the def-store request carries no
        namespace filter (the startup/rebuild backfill path)."""
        _pool, conn = mock_pool
        service.schema_manager.ensure_term_relations_table = AsyncMock(
            side_effect=lambda ns: f'"{ns}"."term_relations"'
        )

        rel_wip = dict(SAMPLE_RELATIONSHIP)
        rel_ct = dict(SAMPLE_RELATIONSHIP, namespace="clintrial")

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([rel_wip, rel_ct]))
            mock_client_cls.return_value = mock_client

            result = await service.batch_sync_term_relations()

        assert result["synced"] == 2
        request_params = mock_client.get.call_args.kwargs["params"]
        assert "namespace" not in request_params
        ensured = {c.args[0] for c in service.schema_manager.ensure_term_relations_table.call_args_list}
        assert ensured == {"wip", "clintrial"}
        insert_targets = [c.args[0] for c in conn.execute.call_args_list]
        assert any('"wip"."term_relations"' in sql for sql in insert_targets)
        assert any('"clintrial"."term_relations"' in sql for sql in insert_targets)

    @pytest.mark.asyncio
    async def test_explicit_namespace_ensures_table_even_when_empty(self, service, mock_pool):
        """An explicit-namespace sync ensures the table before fetching, so
        SQL readers see an empty table rather than relation-does-not-exist
        when the namespace has no relations."""
        _pool, _conn = mock_pool

        with patch("reporting_sync.batch_sync.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_make_api_response([]))
            mock_client_cls.return_value = mock_client

            result = await service.batch_sync_term_relations(namespace="clintrial")

        assert result["synced"] == 0
        service.schema_manager.ensure_term_relations_table.assert_awaited_once_with("clintrial")


# =========================================================================
# _run_batch_sync — zero-document materialization (CASE-636)
# =========================================================================


class TestBatchSyncZeroDocuments:
    """A batch sync over a template with zero documents must still create the
    (empty) table in the template's own namespace: a freshly bootstrapped
    namespace has to be SQL-queryable — 'no rows yet' is an empty table, not
    relation-does-not-exist."""

    @pytest.mark.asyncio
    async def test_zero_documents_still_ensures_template_namespace_table(self, service):
        from reporting_sync.batch_sync import BatchSyncJob, BatchSyncStatus

        service.schema_manager.ensure_table_for_template = AsyncMock(
            return_value='"wip-val"."doc_val_template"'
        )
        service._fetch_template_by_value = AsyncMock(
            return_value={
                "template_id": "TPL-001",
                "value": "VAL_TEMPLATE",
                "namespace": "wip-val",
                "fields": [{"name": "name", "type": "string"}],
            }
        )
        service._fetch_documents = AsyncMock(return_value=([], 0))

        job = BatchSyncJob(
            job_id="t636", template_value="VAL_TEMPLATE", status=BatchSyncStatus.PENDING
        )
        await service._run_batch_sync(job, force=False, page_size=100)

        assert job.status == BatchSyncStatus.COMPLETED
        assert job.total_documents == 0
        service.schema_manager.ensure_table_for_template.assert_awaited_once()
        ns_arg, template_arg = service.schema_manager.ensure_table_for_template.call_args.args
        assert ns_arg == "wip-val"
        assert template_arg["value"] == "VAL_TEMPLATE"


# =========================================================================
# _initial_metadata_sync (startup backfill)
# =========================================================================


class TestInitialMetadataSync:
    """The startup metadata sync must backfill term relations too: they
    otherwise sync only on live NATS events, so a rebuilt reporting database
    silently loses all historical relations."""

    @pytest.mark.asyncio
    async def test_startup_sync_includes_term_relations(self):
        from reporting_sync.main import _initial_metadata_sync

        batch_service = MagicMock()
        batch_service.batch_sync_terminologies = AsyncMock(return_value={"synced": 1})
        batch_service.batch_sync_terms = AsyncMock(return_value={"synced": 1})
        batch_service.batch_sync_term_relations = AsyncMock(return_value={"synced": 1})
        batch_service.batch_sync_templates = AsyncMock(return_value={"synced": 1})

        with patch("reporting_sync.main.retry_async", new=AsyncMock()):
            await _initial_metadata_sync(batch_service)

        batch_service.batch_sync_terminologies.assert_awaited_once()
        batch_service.batch_sync_terms.assert_awaited_once()
        # No namespace argument: the backfill must sweep ALL namespaces.
        batch_service.batch_sync_term_relations.assert_awaited_once_with()
