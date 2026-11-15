"""A namespace whose deletion has begun is closed in reporting (CASE-1237).

Privileged SQL runs on the reader pool, which reads every namespace schema.
It kept a deleted namespace's rows readable until the deletion's drop step
— for the whole deletion, and after the namespace record was gone while the
drop waited for reporting-sync. Closing revokes USAGE on the schema from
every role but its owner, so nothing reads it any more, by name or through a
function; nothing writes into it or reuses it; and a restart neither
re-grants it nor serves privileged SQL before it has closed what the
Registry is deleting.

The test login is a superuser, which ignores USAGE; reads go through a pool
that logs in as the real reader role, the one privileged SQL uses.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from reporting_sync import main
from reporting_sync.main import _close_namespaces_being_deleted, app, init_postgres_schema, state
from reporting_sync.pg_roles import RoleSpec, provision_roles
from reporting_sync.schema_manager import CLOSED_SCHEMA_COMMENT, NamespaceDeletedError, SchemaManager

from .conftest import POSTGRES_TEST_URI, _drop_test_roles, requires_postgres

pytestmark = [requires_postgres, pytest.mark.asyncio]

ADMIN = os.environ["WIP_AUTH_LEGACY_API_KEY"]
READER_KEY = "rs_reader_key"  # a namespace-scoped key (conftest)
NS = "ns-closing"


@pytest_asyncio.fixture
async def roles(pg_pool):
    """Owner and reader roles, a namespace schema owned by the owner and read
    by the reader as the startup grant loop grants it, and a pool logged in
    as the reader."""
    async with pg_pool.acquire() as conn:
        await _drop_test_roles(conn)
    suffix = uuid.uuid4().hex[:8]
    spec = RoleSpec(
        owner=f"rs_owner_{suffix}", owner_password=f"o-{suffix}",
        reader=f"rs_reader_{suffix}", reader_password=f"r-{suffix}",
        manager=f"rs_roles_{suffix}", manager_password=f"m-{suffix}",
    )
    await init_postgres_schema(pg_pool)
    async with pg_pool.acquire() as conn:
        await provision_roles(conn, spec)
        await conn.execute(f'CREATE SCHEMA "{NS}" AUTHORIZATION "{spec.owner}"')
        await conn.execute(f'CREATE TABLE "{NS}".doc_secret (k TEXT)')
        await conn.execute(f'ALTER TABLE "{NS}".doc_secret OWNER TO "{spec.owner}"')
        await conn.execute(f'INSERT INTO "{NS}".doc_secret VALUES (\'locked-row\')')
        await provision_roles(conn, spec)  # the startup grant loop: reader gets USAGE
    parts = urlsplit(POSTGRES_TEST_URI)
    reader_pool = await asyncpg.create_pool(
        host=parts.hostname or "localhost", port=parts.port or 5432,
        database=parts.path.lstrip("/"), user=spec.reader, password=spec.reader_password,
        min_size=1, max_size=2,
    )
    try:
        yield spec, reader_pool
    finally:
        await reader_pool.close()
        async with pg_pool.acquire() as conn:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{NS}" CASCADE')
            await _drop_test_roles(conn)


async def _reader_sees(reader_pool, sql: str) -> bool:
    try:
        async with reader_pool.acquire() as conn:
            return "locked-row" in str(await conn.fetchval(sql))
    except asyncpg.InsufficientPrivilegeError:
        return False


READS = [
    f'SELECT k FROM "{NS}".doc_secret',
    f"SELECT query_to_xml('SELECT k FROM \"{NS}\".doc_secret', true, false, '')",
]


async def _usage(pg_pool, role: str) -> bool:
    async with pg_pool.acquire() as conn:
        return await conn.fetchval("SELECT has_schema_privilege($1, $2, 'USAGE')", role, NS)


async def test_closing_takes_every_read_away(pg_pool, roles):
    spec, reader_pool = roles
    for sql in READS:
        assert await _reader_sees(reader_pool, sql), sql  # open before

    assert await SchemaManager(pg_pool).close_namespace(NS) is True

    for sql in READS:
        assert not await _reader_sees(reader_pool, sql), sql
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT obj_description(oid, 'pg_namespace') FROM pg_namespace WHERE nspname = $1", NS,
        ) == CLOSED_SCHEMA_COMMENT
        assert await conn.fetchval(
            "SELECT 1 FROM public._wip_deleted_namespaces WHERE namespace = $1", NS,
        )
    # Idempotent; the owner keeps its schema, so the drop can still run.
    assert await SchemaManager(pg_pool).close_namespace(NS) is True
    assert await _usage(pg_pool, spec.owner)


async def test_a_closed_schema_is_not_reused(pg_pool, roles):
    sm = SchemaManager(pg_pool)
    assert await sm.ensure_schema(NS) == NS
    await sm.close_namespace(NS)
    with pytest.raises(NamespaceDeletedError):
        await sm.ensure_schema(NS)
    assert NS not in await sm.open_namespace_schemas()


async def test_a_restart_does_not_grant_a_closed_schema_again(pg_pool, roles):
    spec, reader_pool = roles
    await SchemaManager(pg_pool).close_namespace(NS)
    async with pg_pool.acquire() as conn:
        await provision_roles(conn, spec)
    assert not await _usage(pg_pool, spec.reader)
    assert not await _reader_sees(reader_pool, READS[0])


async def test_a_namespace_that_never_had_a_schema_is_closed_too(pg_pool):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool, namespace_status=_status({"ns-none": "locked"}))
    assert await sm.close_namespace("ns-none") is False
    with pytest.raises(NamespaceDeletedError):
        await sm.ensure_schema("ns-none")


def _status(answers: dict[str, str | None]):
    async def lookup(namespace: str) -> str | None:
        return answers.get(namespace, "active")
    return lookup


async def test_startup_closes_what_the_registry_is_deleting(pg_pool, roles, monkeypatch):
    _spec, reader_pool = roles
    sm = SchemaManager(pg_pool)
    for ns in ("ns-gone", "ns-live"):
        await sm.ensure_schema(ns)
    # The check looks at the schemas its own pool's role owns, as the owner
    # pool owns every namespace schema in a deployment.
    async with pg_pool.acquire() as conn:
        await conn.execute(f'ALTER SCHEMA "{NS}" OWNER TO CURRENT_USER')
    monkeypatch.setattr(state, "deletions_closed", False)
    try:
        await _close_namespaces_being_deleted(
            pg_pool, status_lookup=_status({NS: "locked", "ns-gone": None, "wip": "active"}),
        )
        assert state.deletions_closed is True
        assert not await _reader_sees(reader_pool, READS[0])
        still_open = await sm.open_namespace_schemas()
        assert "ns-live" in still_open and "ns-gone" not in still_open and NS not in still_open
    finally:
        async with pg_pool.acquire() as conn:
            for ns in ("ns-gone", "ns-live"):
                await conn.execute(f'DROP SCHEMA IF EXISTS "{ns}" CASCADE')


async def test_startup_closes_nothing_on_a_registry_that_cannot_see_namespaces(pg_pool, roles, monkeypatch):
    """A lookup whose 'wip' is not active cannot tell live from gone."""
    _spec, reader_pool = roles

    class Stop(Exception):
        pass

    async def stop(_delay):
        raise Stop

    monkeypatch.setattr(main, "_postgres_sleep", stop)
    monkeypatch.setattr(state, "deletions_closed", False)
    with pytest.raises(Stop):
        await _close_namespaces_being_deleted(pg_pool, status_lookup=_status({"wip": None, NS: None}))
    assert state.deletions_closed is False
    assert await _reader_sees(reader_pool, READS[0])


def _client(key: str) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": key})


@pytest_asyncio.fixture
async def app_on_reader(pg_pool, roles, monkeypatch):
    """The app with privileged SQL on the real reader role."""
    _spec, reader_pool = roles
    monkeypatch.setattr(state, "postgres_pool", pg_pool)
    monkeypatch.setattr(state, "read_pool", reader_pool)
    monkeypatch.setattr(state, "deletions_closed", True)
    return reader_pool


async def test_the_close_route_ends_privileged_sql_on_the_namespace(app_on_reader):
    async with _client(ADMIN) as c:
        before = await c.post("/api/reporting-sync/query", json={"sql": READS[0]})
        assert before.status_code == 200 and "locked-row" in before.text
        closed = await c.post(f"/api/reporting-sync/namespace/{NS}/close")
        assert closed.status_code == 200, closed.text
        assert closed.json()["closed"] is True
        for sql in READS:
            r = await c.post("/api/reporting-sync/query", json={"sql": sql})
            assert "locked-row" not in r.text and r.status_code == 400, r.text
        export = await c.post("/api/reporting-sync/export/csv", json={"sql": READS[0]})
        assert "locked-row" not in export.text and export.status_code >= 400
        view = await c.post(f"/api/reporting-sync/namespace/{NS}/views", json={"name": "v", "sql": READS[0]})
        assert view.status_code == 404, view.text


async def test_only_a_privileged_key_closes(app_on_reader):
    async with _client(READER_KEY) as c:
        assert (await c.post(f"/api/reporting-sync/namespace/{NS}/close")).status_code == 403


async def test_privileged_sql_waits_for_the_startup_check(app_on_reader, monkeypatch):
    monkeypatch.setattr(state, "deletions_closed", False)
    async with _client(ADMIN) as c:
        r = await c.post("/api/reporting-sync/query", json={"sql": "SELECT 1"})
    assert r.status_code == 503 and "being deleted" in r.text
