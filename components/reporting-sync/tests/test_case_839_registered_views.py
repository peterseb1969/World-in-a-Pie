"""Registered namespace views — the /namespace/{ns}/views endpoints.

Covers the hardened registration surface: single-statement read-only
validation (prepared EXPLAIN subselect under search_path), reserved
platform relation names, ownership checks (409 on occupied names),
DROP+CREATE replace semantics, and the transactional delete that keeps
the bookkeeping row when the DROP fails. Provenance: CASE-839.
"""

from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from reporting_sync.main import app, init_postgres_schema, state
from reporting_sync.schema_manager import SchemaManager

from .conftest import requires_postgres

# =========================================================================
# Fixtures (same idiom as test_query.py)
# =========================================================================


# Postgres 2BP01 (dependent objects still exist) — asyncpg's own class
# carries the sqlstate the endpoints branch on.
_DependentObjectsError = asyncpg.exceptions.DependentObjectsStillExistError


def _mock_conn():
    """AsyncMock connection with a working transaction() context manager."""
    conn = AsyncMock()
    txn = MagicMock()
    txn.__aenter__ = AsyncMock()
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock()
    return conn


def _mock_pool():
    pool = MagicMock()
    conn = _mock_conn()
    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm
    return pool, conn


@pytest.fixture
def mock_state():
    pool, conn = _mock_pool()
    original_pool = state.postgres_pool
    state.postgres_pool = pool
    yield pool, conn
    state.postgres_pool = original_pool


@pytest.fixture
def http_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _executed_sql(conn) -> list[str]:
    return [str(c.args[0]) for c in conn.execute.call_args_list]


# =========================================================================
# POST /namespace/{ns}/views — validation gates
# =========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [
    "doc_study",            # entity-view / doc-table prefix
    "doc_study__v1",        # per-version table
    "anything__v12",        # per-version suffix on any base
    "my_view__entities",    # entity-core view suffix
    "terms",                # fixed metadata table
    "term_relations",       # fixed metadata table
    "_wip_views",           # bookkeeping prefix
])
async def test_register_rejects_reserved_names(http_client, mock_state, name):
    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": name, "sql": "SELECT 1"},
        )
    assert resp.status_code == 400
    assert "reserved" in resp.json()["detail"].lower() or "collides" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_register_rejects_write_keywords(http_client, mock_state):
    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_x", "sql": "SELECT 1; DROP TABLE t"},
        )
    assert resp.status_code == 400
    assert "read-only" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_register_rejects_empty_sql(http_client, mock_state):
    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_x", "sql": " ;; "},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_register_rejects_occupied_unregistered_name(http_client, mock_state):
    """A relation that exists but is not in the bookkeeping is not ours to
    replace — registering over it must 409, not hijack it."""
    _pool, conn = mock_state
    # 1st fetchval: not in _wip_views; 2nd: pg_class says the name exists.
    conn.fetchval = AsyncMock(side_effect=[None, True])
    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_taken", "sql": "SELECT 1"},
        )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_register_invalid_sql_is_400_and_creates_nothing(http_client, mock_state):
    _pool, conn = mock_state
    conn.fetchval = AsyncMock(side_effect=[None, None])
    conn.fetch = AsyncMock(side_effect=asyncpg.PostgresError("relation does not exist"))
    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_bad", "sql": "SELECT * FROM nope"},
        )
    assert resp.status_code == 400
    assert "invalid" in resp.json()["detail"].lower()
    assert not any("CREATE VIEW" in s for s in _executed_sql(conn))


# =========================================================================
# POST — happy paths
# =========================================================================


@pytest.mark.asyncio
async def test_register_new_view(http_client, mock_state):
    _pool, conn = mock_state
    conn.fetchval = AsyncMock(side_effect=[None, None])  # not registered, name free
    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_study_search", "sql": "SELECT 1;"},
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["created"] is True
    assert data["schema_qualified"] == '"clintrial"."v_study_search"'

    executed = _executed_sql(conn)
    # Validation is a PREPARED subselect (fetch), enforcing one statement.
    explain_calls = [str(c.args[0]) for c in conn.fetch.call_args_list]
    assert any(
        s.startswith("EXPLAIN SELECT * FROM (") for s in explain_calls
    ), explain_calls
    # Name resolution pinned to the namespace schema on both paths.
    assert sum('SET LOCAL search_path = "clintrial", public' in s for s in executed) >= 2
    # Plain CREATE (no OR REPLACE — replace is DROP+CREATE), trailing ';' stripped.
    assert any(
        s == 'CREATE VIEW "clintrial"."v_study_search" AS SELECT 1' for s in executed
    ), executed
    assert not any("CREATE OR REPLACE" in s for s in executed)
    # First registration of a free name never issues a DROP.
    assert not any(s.startswith("DROP VIEW") for s in executed)


@pytest.mark.asyncio
async def test_reregister_replaces_via_drop_create(http_client, mock_state):
    _pool, conn = mock_state
    conn.fetchval = AsyncMock(side_effect=[True])  # already registered
    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_study_search", "sql": "SELECT 2 AS other_col"},
        )
    assert resp.status_code == 200
    assert resp.json()["created"] is False
    executed = _executed_sql(conn)
    # DROP + CREATE lets the column set change (CREATE OR REPLACE cannot).
    drop_idx = next(
        i for i, s in enumerate(executed)
        if s == 'DROP VIEW IF EXISTS "clintrial"."v_study_search"'
    )
    create_idx = next(i for i, s in enumerate(executed) if s.startswith("CREATE VIEW"))
    assert drop_idx < create_idx


@pytest.mark.asyncio
async def test_register_dependency_failure_is_409(http_client, mock_state):
    _pool, conn = mock_state
    conn.fetchval = AsyncMock(side_effect=[True])
    # The DROP of the replaced view hits a dependent (2BP01).
    async def _execute(sql, *args):
        if sql.startswith("DROP VIEW"):
            raise _DependentObjectsError("view v_other depends on it")
    conn.execute = AsyncMock(side_effect=_execute)
    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_base", "sql": "SELECT 1"},
        )
    assert resp.status_code == 409


# =========================================================================
# DELETE /namespace/{ns}/views/{name}
# =========================================================================


@pytest.mark.asyncio
async def test_delete_unknown_view_is_404(http_client, mock_state):
    _pool, conn = mock_state
    conn.fetchval = AsyncMock(return_value=None)
    async with http_client:
        resp = await http_client.delete(
            "/api/reporting-sync/namespace/clintrial/views/v_missing"
        )
    assert resp.status_code == 404
    assert not any("DELETE FROM public._wip_views" in s for s in _executed_sql(conn))


@pytest.mark.asyncio
async def test_delete_drops_before_deleting_registration(http_client, mock_state):
    _pool, conn = mock_state
    conn.fetchval = AsyncMock(return_value=True)
    async with http_client:
        resp = await http_client.delete(
            "/api/reporting-sync/namespace/clintrial/views/v_x"
        )
    assert resp.status_code == 200
    assert resp.json()["dropped"] is True
    executed = _executed_sql(conn)
    drop_idx = next(i for i, s in enumerate(executed) if s.startswith("DROP VIEW"))
    del_idx = next(i for i, s in enumerate(executed) if "DELETE FROM public._wip_views" in s)
    assert drop_idx < del_idx


@pytest.mark.asyncio
async def test_delete_dependency_failure_is_409_and_keeps_registration(
    http_client, mock_state
):
    _pool, conn = mock_state
    conn.fetchval = AsyncMock(return_value=True)
    async def _execute(sql, *args):
        if sql.startswith("DROP VIEW"):
            raise _DependentObjectsError("v_child depends on v_parent")
    conn.execute = AsyncMock(side_effect=_execute)
    async with http_client:
        resp = await http_client.delete(
            "/api/reporting-sync/namespace/clintrial/views/v_parent"
        )
    assert resp.status_code == 409
    # The transaction wraps DROP before DELETE, so the bookkeeping row was
    # never deleted on the failure path (DROP raised first).
    assert not any("DELETE FROM public._wip_views" in s for s in _executed_sql(conn))


# =========================================================================
# GET /namespace/{ns}/views
# =========================================================================


@pytest.mark.asyncio
async def test_list_views(http_client, mock_state):
    _pool, conn = mock_state
    conn.fetch = AsyncMock(return_value=[
        {
            "namespace": "clintrial", "view_name": "v_study_search",
            "view_sql": "SELECT 1", "registered_at": None, "registered_by": "bootstrap",
        },
    ])
    async with http_client:
        resp = await http_client.get("/api/reporting-sync/namespace/clintrial/views")
    assert resp.status_code == 200
    data = resp.json()
    assert data["namespace"] == "clintrial"
    assert data["views"][0]["view_name"] == "v_study_search"


# =========================================================================
# SchemaManager — reserved names
# =========================================================================


@pytest.mark.parametrize("name,reserved", [
    ("doc_patient", True),
    ("DOC_PATIENT", True),          # case-insensitive
    ("doc_patient__v3", True),
    ("x__v10", True),
    ("x__entities", True),
    ("terminologies", True),
    ("templates", True),
    ("_wip_schema_migrations", True),
    ("v_study_search", False),
    ("study_summary", False),
    ("documented", False),          # 'doc' prefix without underscore is fine
    ("x__version", False),          # __v must be followed by digits
])
def test_is_reserved_relation_name(name, reserved):
    assert SchemaManager.is_reserved_relation_name(name) is reserved


# =========================================================================
# Integration — real PostgreSQL (requires POSTGRES_TEST_URI)
# =========================================================================

@pytest_asyncio.fixture
async def real_state(pg_pool):
    """Point the app's global state at the real test pool."""
    await init_postgres_schema(pg_pool)
    original_pool = state.postgres_pool
    state.postgres_pool = pg_pool
    yield pg_pool
    state.postgres_pool = original_pool


async def _seed_version_table(pool, namespace: str = "clintrial") -> None:
    """A minimal per-version table so entity views can be built over it."""
    sm = SchemaManager(pool)
    schema = await sm.ensure_schema(namespace)
    async with pool.acquire() as conn:
        await conn.execute(
            f'CREATE TABLE "{schema}"."doc_x__v1" '
            "(document_id TEXT PRIMARY KEY, title TEXT)"
        )
        await conn.execute(
            f'INSERT INTO "{schema}"."doc_x__v1" VALUES ($1, $2)',
            "d1", "hello",
        )
    await sm.ensure_views_for_template(namespace, "x")


@requires_postgres
@pytest.mark.asyncio
async def test_pg_multistatement_injection_never_executes(http_client, real_state):
    """A second ';'-separated statement that evades the keyword blacklist
    (dynamic SQL in a DO block) must fail validation, not execute."""
    payload = {
        "name": "v_evil",
        "sql": "SELECT 1; DO $$ BEGIN EXECUTE 'CRE'||'ATE TABLE public.pwned()'; END $$",
    }
    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views", json=payload
        )
    assert resp.status_code == 400
    async with real_state.acquire() as conn:
        pwned = await conn.fetchval(
            "SELECT TRUE FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'pwned'"
        )
    assert pwned is None


@requires_postgres
@pytest.mark.asyncio
async def test_pg_unqualified_names_resolve_in_namespace_schema(http_client, real_state):
    """The documented usage: bare doc_<value> in the view SQL resolves in
    the namespace schema via search_path."""
    await _seed_version_table(real_state)
    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_x", "sql": "SELECT document_id, title FROM doc_x"},
        )
        assert resp.status_code == 201, resp.json()
    async with real_state.acquire() as conn:
        rows = await conn.fetch('SELECT * FROM "clintrial"."v_x"')
    assert [dict(r) for r in rows] == [{"document_id": "d1", "title": "hello"}]


@requires_postgres
@pytest.mark.asyncio
async def test_pg_replace_may_change_column_set(http_client, real_state):
    """Re-registration is DROP+CREATE, so a changed column set succeeds
    where CREATE OR REPLACE VIEW would error."""
    async with http_client:
        r1 = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_r", "sql": "SELECT 1 AS a"},
        )
        assert r1.status_code == 201
        r2 = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_r", "sql": "SELECT 2 AS b, 3 AS c"},
        )
        assert r2.status_code == 200, r2.json()
    async with real_state.acquire() as conn:
        row = await conn.fetchrow('SELECT * FROM "clintrial"."v_r"')
    assert dict(row) == {"b": 2, "c": 3}


@requires_postgres
@pytest.mark.asyncio
async def test_pg_entity_view_rebuild_keeps_registered_views(http_client, real_state):
    """A registered view over the entity view must survive an entity-view
    rebuild (pre-fix this raised dependent_objects_still_exist and failed
    the rebuild)."""
    await _seed_version_table(real_state)
    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_over_entity", "sql": "SELECT title FROM doc_x"},
        )
        assert resp.status_code == 201, resp.json()

    sm = SchemaManager(real_state)
    # The rebuild that runs on every new version-table creation.
    warning = await sm.ensure_views_for_template("clintrial", "x")
    assert warning is None

    async with real_state.acquire() as conn:
        rows = await conn.fetch('SELECT * FROM "clintrial"."v_over_entity"')
    assert [dict(r) for r in rows] == [{"title": "hello"}]


@requires_postgres
@pytest.mark.asyncio
async def test_pg_recreation_after_schema_reset(http_client, real_state):
    """After a schema drop (bookkeeping survives in public), recreation
    restores the registered view once its tables are back."""
    await _seed_version_table(real_state)
    async with http_client:
        resp = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_x", "sql": "SELECT title FROM doc_x"},
        )
        assert resp.status_code == 201

    sm = SchemaManager(real_state)
    await sm.drop_namespace_schema("clintrial")
    # Tables come back (restore / batch sync), then recreation runs.
    await _seed_version_table(real_state)
    present, failed = await sm.recreate_registered_views("clintrial")
    assert (present, failed) == (1, 0)
    async with real_state.acquire() as conn:
        rows = await conn.fetch('SELECT * FROM "clintrial"."v_x"')
    assert [dict(r) for r in rows] == [{"title": "hello"}]


@requires_postgres
@pytest.mark.asyncio
async def test_pg_delete_with_dependent_is_409_then_resolvable(http_client, real_state):
    """Deleting a view another registered view depends on is a 409 that
    keeps the registration; deleting dependent-first succeeds."""
    async with http_client:
        r1 = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_base", "sql": "SELECT 1 AS a"},
        )
        assert r1.status_code == 201
        r2 = await http_client.post(
            "/api/reporting-sync/namespace/clintrial/views",
            json={"name": "v_child", "sql": "SELECT a FROM v_base"},
        )
        assert r2.status_code == 201, r2.json()

        blocked = await http_client.delete(
            "/api/reporting-sync/namespace/clintrial/views/v_base"
        )
        assert blocked.status_code == 409
        listing = await http_client.get(
            "/api/reporting-sync/namespace/clintrial/views"
        )
        assert {v["view_name"] for v in listing.json()["views"]} == {"v_base", "v_child"}

        ok_child = await http_client.delete(
            "/api/reporting-sync/namespace/clintrial/views/v_child"
        )
        assert ok_child.status_code == 200
        ok_base = await http_client.delete(
            "/api/reporting-sync/namespace/clintrial/views/v_base"
        )
        assert ok_base.status_code == 200
