"""SQL from a namespace-scoped key reads only that key's namespaces (CASE-996).

A non-privileged key's query, query export and view read run on a
connection that logs in as a role able to read only the key's namespaces,
so PostgreSQL refuses everything else. The escapes tested here are the ones
a check on the SQL text cannot see: functions that read whatever their
string names (``query_to_xml``) and switching the session's role
(``set_config('role', …)``).

Runs against the real test PostgreSQL (the ``scoped_reads`` fixture in
conftest provides the role manager and cleans up the roles).
"""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from reporting_sync import scoped_reads
from reporting_sync.main import app, init_postgres_schema, state
from reporting_sync.pg_roles import is_privileged
from reporting_sync.schema_manager import SchemaManager
from wip_auth import clear_permission_cache

from .conftest import requires_postgres

pytestmark = requires_postgres

ADMIN = os.environ["WIP_AUTH_LEGACY_API_KEY"]
READER = "rs_reader_key"  # read on ns-a
NSADMIN = "rs_nsadmin_key"  # admin on ns-a
XREADER = "rs_xreader_key"  # read on ns-a and ns-other

P = "/api/reporting-sync"
SECRET = '"ns-other"."doc_secret__v1"'


def _client(key: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": key},
    )


async def _query(key: str, sql: str, **extra):
    async with _client(key) as c:
        return await c.post(f"{P}/query", json={"sql": sql, **extra})


@pytest_asyncio.fixture
async def scoped(pg_pool, scoped_reads):
    """The app on the test pool, with scoped reads and two namespaces."""
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    own = await sm.ensure_schema("ns-a")
    other = await sm.ensure_schema("ns-other")
    async with pg_pool.acquire() as conn:
        await conn.execute(f'CREATE TABLE "{own}"."doc_probe__v1" (document_id TEXT, title TEXT)')
        await conn.execute(f'INSERT INTO "{own}"."doc_probe__v1" VALUES (\'d1\', \'alpha\')')
        await conn.execute(f'CREATE TABLE "{other}"."doc_secret__v1" (document_id TEXT)')
        await conn.execute(f'INSERT INTO "{other}"."doc_secret__v1" VALUES (\'SECRET-1\')')
    saved = state.postgres_pool, state.read_pool
    state.postgres_pool = state.read_pool = pg_pool
    clear_permission_cache()
    yield scoped_reads
    state.postgres_pool, state.read_pool = saved
    clear_permission_cache()
    async with pg_pool.acquire() as conn:
        for schema in ("ns-a", "ns-other"):
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest.mark.asyncio
async def test_scoped_key_reads_its_own_namespace(scoped):
    r = await _query(READER, "SELECT document_id, title FROM doc_probe__v1", namespace="ns-a")
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [{"document_id": "d1", "title": "alpha"}]


@pytest.mark.asyncio
async def test_scoped_key_cannot_read_another_namespace(scoped):
    r = await _query(READER, f"SELECT * FROM {SECRET}")
    assert r.status_code == 400, r.text
    assert "permission denied" in r.text
    assert "SECRET-1" not in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", [
    # A function reads whatever its string names; nothing in the SQL text
    # names the other schema as a relation.
    f"SELECT query_to_xml('select * from {SECRET}', true, false, '') AS x",
    # Switching back to the login role, then reading through a function.
    "SELECT set_config('role', 'none', true) AS r, "
    f"query_to_xml('select * from {SECRET}', true, false, '') AS x",
])
async def test_function_reads_cannot_leave_the_scope(scoped, sql):
    r = await _query(READER, sql)
    assert "SECRET-1" not in r.text
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_a_scoped_session_cannot_switch_to_a_wider_role(scoped, pg_pool):
    # A key reading both namespaces brings ns-other's roles into existence,
    # so the refusals below are permission refusals, not missing roles.
    assert (await _query(XREADER, "SELECT 1")).status_code == 200
    async with scoped.connection(["ns-other"]):
        pass
    async with pg_pool.acquire() as conn:
        reader = await conn.fetchval(
            "SELECT rolname FROM pg_roles WHERE rolname LIKE 'rs_reader_%' LIMIT 1"
        )
    for role in (reader, scoped_reads.namespace_role("ns-other"), scoped_reads.scope_role(["ns-other"])):
        r = await _query(READER, f"SELECT set_config('role', '{role}', true)")
        assert r.status_code == 400, (role, r.text)
        assert "permission denied" in r.text


@pytest.mark.asyncio
async def test_a_key_reading_both_namespaces_reads_both(scoped):
    r = await _query(XREADER, f"SELECT document_id FROM {SECRET}")
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [{"document_id": "SECRET-1"}]


@pytest.mark.asyncio
async def test_privileged_key_reads_every_namespace(scoped):
    r = await _query(ADMIN, f"SELECT document_id FROM {SECRET}")
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_a_named_namespace_must_be_readable(scoped):
    r = await _query(READER, "SELECT 1", namespace="ns-other")
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_query_export_is_scoped(scoped):
    async with _client(READER) as c:
        refused = await c.post(f"{P}/export/csv", json={"sql": f"SELECT * FROM {SECRET}"})
        own = await c.post(
            f"{P}/export/csv", json={"sql": "SELECT document_id FROM doc_probe__v1", "namespace": "ns-a"},
        )
    assert refused.status_code == 400, refused.text
    assert "SECRET-1" not in refused.text
    assert own.status_code == 200, own.text
    assert own.text.splitlines() == ["document_id", "d1"]


@pytest.mark.asyncio
async def test_a_view_cannot_smuggle_a_read_through_a_function(scoped):
    """The view's dependencies name nothing outside ns-a, so registration
    passes; reading it must still not reach ns-other."""
    sql = f"SELECT query_to_xml('select * from {SECRET}', true, false, '') AS x"
    async with _client(NSADMIN) as c:
        r = await c.post(f"{P}/namespace/ns-a/views", json={"name": "v_smuggle", "sql": sql})
    assert r.status_code == 201, r.text
    async with _client(READER) as c:
        r = await c.post(f"{P}/namespace/ns-a/views/v_smuggle/rows", json={})
    assert "SECRET-1" not in r.text
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_view_validation_cannot_read_another_namespace_while_planning(scoped):
    """Planning evaluates stable functions while estimating selectivity, so
    an EXPLAIN alone runs table_to_xml; a failing cast then puts what it read
    into the error. Registration must plan on the caller's own connection."""
    sql = (
        "SELECT 1 AS n FROM doc_probe__v1 WHERE length(title) = "
        f"(table_to_xml('{SECRET}'::regclass, true, false, ''))::text::int"
    )
    async with _client(NSADMIN) as c:
        r = await c.post(f"{P}/namespace/ns-a/views", json={"name": "v_explain", "sql": sql})
    assert "SECRET-1" not in r.text
    assert r.status_code == 403, r.text
    assert "permission denied" in r.text


@pytest.mark.asyncio
async def test_registered_views_read_with_the_readers_privileges(scoped):
    """A view a privileged key registered over another namespace reads, for a
    scoped key's own SQL, only what that key could read directly."""
    async with _client(ADMIN) as c:
        r = await c.post(
            f"{P}/namespace/ns-a/views", json={"name": "v_cross", "sql": f"SELECT * FROM {SECRET}"},
        )
    assert r.status_code == 201, r.text
    r = await _query(READER, 'SELECT * FROM "ns-a"."v_cross"')
    assert r.status_code == 400, r.text
    assert "SECRET-1" not in r.text
    r = await _query(XREADER, 'SELECT * FROM "ns-a"."v_cross"')
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_startup_switches_existing_registered_views_to_invoker(scoped, pg_pool):
    async with pg_pool.acquire() as conn:
        await conn.execute('CREATE VIEW "ns-a"."v_old" AS SELECT * FROM "ns-a"."doc_probe__v1"')
        await conn.execute(
            "INSERT INTO public._wip_views (namespace, view_name, view_sql, registered_by) "
            "VALUES ('ns-a', 'v_old', 'SELECT * FROM doc_probe__v1', 'test')"
        )
    await SchemaManager(pg_pool).recreate_registered_views("ns-a")
    async with pg_pool.acquire() as conn:
        options = await conn.fetchval(
            "SELECT reloptions FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'ns-a' AND c.relname = 'v_old'"
        )
    assert "security_invoker=true" in options


@pytest.mark.asyncio
async def test_tables_created_later_are_readable_in_scope(scoped, pg_pool):
    assert (await _query(READER, "SELECT 1")).status_code == 200
    async with pg_pool.acquire() as conn:
        await conn.execute('CREATE TABLE "ns-a"."doc_late__v1" AS SELECT 42 AS n')
    r = await _query(READER, 'SELECT n FROM "ns-a"."doc_late__v1"')
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [{"n": 42}]


@pytest.mark.asyncio
async def test_scope_roles_are_unprivileged_and_the_manager_is_not_a_member(scoped, pg_pool):
    assert (await _query(READER, "SELECT 1")).status_code == 200
    scope = scoped_reads.scope_role(["ns-a"])
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls FROM pg_roles WHERE rolname = $1",
            scope,
        )
        assert row is not None and not any(row.values())
        assert not await conn.fetchval(
            "SELECT pg_has_role($1, $2, 'USAGE')", scoped.manager_user, scope,
        )
    async with scoped.connection(["ns-a"]) as conn:
        assert not await is_privileged(conn)
        assert await conn.fetchval("SELECT current_user") == scope


@pytest.mark.asyncio
async def test_without_a_role_manager_scoped_sql_is_unavailable(scoped):
    state.scoped_reads = None
    r = await _query(READER, "SELECT 1")
    assert r.status_code == 503, r.text
    assert (await _query(ADMIN, "SELECT 1")).status_code == 200
