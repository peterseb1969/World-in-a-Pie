"""reporting-sync serves requests as unprivileged roles (CASE-851, Phase 2b C8).

Runs against the real test PostgreSQL, whose login (``test``) is a
superuser and so plays the bootstrap role. Roles are cluster-wide, so each
test provisions uniquely named roles and removes them afterwards.
"""

import uuid
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from reporting_sync import main as rs_main
from reporting_sync.pg_roles import RoleSpec, is_privileged, provision_roles

from .conftest import POSTGRES_TEST_URI, requires_postgres

pytestmark = requires_postgres


def _dsn_as(user: str, password: str) -> str:
    parts = urlsplit(POSTGRES_TEST_URI)
    host = parts.hostname or "localhost"
    netloc = f"{user}:{password}@{host}:{parts.port or 5432}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@pytest.fixture
async def spec(pg_pool):
    suffix = uuid.uuid4().hex[:8]
    role_spec = RoleSpec(
        owner=f"rs_owner_{suffix}", owner_password=f"o-{suffix}",
        reader=f"rs_reader_{suffix}", reader_password=f"r-{suffix}",
    )
    yield role_spec
    async with pg_pool.acquire() as conn:
        for role in (role_spec.reader, role_spec.owner):
            exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
            if exists:
                await conn.execute(f'REASSIGN OWNED BY "{role}" TO CURRENT_USER')
                await conn.execute(f'DROP OWNED BY "{role}"')
                await conn.execute(f'DROP ROLE "{role}"')


@pytest.mark.asyncio
async def test_provisioned_roles_are_unprivileged(pg_pool, spec):
    async with pg_pool.acquire() as conn:
        report = await provision_roles(conn, spec)
        assert sorted(report.roles_created) == sorted([spec.owner, spec.reader])
        rows = await conn.fetch(
            "SELECT rolname, rolsuper, rolcreaterole, rolcreatedb FROM pg_roles "
            "WHERE rolname = ANY($1::text[])",
            [spec.owner, spec.reader],
        )
    assert len(rows) == 2
    assert not any(r["rolsuper"] or r["rolcreaterole"] or r["rolcreatedb"] for r in rows)


@pytest.mark.asyncio
async def test_owner_can_do_the_sync_pipelines_ddl(pg_pool, spec):
    async with pg_pool.acquire() as conn:
        await provision_roles(conn, spec)
    owner = await asyncpg.connect(_dsn_as(spec.owner, spec.owner_password))
    try:
        assert not await is_privileged(owner)
        await owner.execute('CREATE SCHEMA "ns-roles"')
        await owner.execute('CREATE TABLE "ns-roles".doc_x__v1 (document_id TEXT PRIMARY KEY)')
        await owner.execute('ALTER TABLE "ns-roles".doc_x__v1 ADD COLUMN "title" TEXT')
        await owner.execute('CREATE INDEX doc_x_title_idx ON "ns-roles".doc_x__v1 (title)')
        await owner.execute('CREATE VIEW "ns-roles".doc_x AS SELECT * FROM "ns-roles".doc_x__v1')
        await owner.execute('CREATE TABLE IF NOT EXISTS public._wip_rs_probe (k TEXT)')
        await owner.execute('DROP TABLE public._wip_rs_probe')
        await owner.execute('DROP SCHEMA "ns-roles" CASCADE')
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_reader_reads_owner_objects_but_cannot_write_or_escalate(pg_pool, spec):
    async with pg_pool.acquire() as conn:
        await provision_roles(conn, spec)
    owner = await asyncpg.connect(_dsn_as(spec.owner, spec.owner_password))
    reader = await asyncpg.connect(_dsn_as(spec.reader, spec.reader_password))
    try:
        # created by the owner AFTER provisioning: default privileges apply
        await owner.execute('CREATE SCHEMA "ns-read"')
        await owner.execute('CREATE TABLE "ns-read".t (v INT)')
        await owner.execute('INSERT INTO "ns-read".t VALUES (1)')
        await owner.execute('CREATE VIEW "ns-read".v AS SELECT v FROM "ns-read".t')

        assert not await is_privileged(reader)
        assert await reader.fetchval('SELECT v FROM "ns-read".t') == 1
        assert await reader.fetchval('SELECT v FROM "ns-read".v') == 1

        with pytest.raises(asyncpg.PostgresError):
            await reader.execute('INSERT INTO "ns-read".t VALUES (2)')
        with pytest.raises(asyncpg.PostgresError):
            await reader.execute('CREATE TABLE "ns-read".u (v INT)')
        with pytest.raises(asyncpg.PostgresError):
            await reader.execute(f'SET ROLE "{spec.owner}"')
        with pytest.raises(asyncpg.PostgresError):
            await reader.execute(
                f"SELECT set_config('role', '{spec.owner}', false)"
            )
    finally:
        await reader.close()
        await owner.execute('DROP SCHEMA "ns-read" CASCADE')
        await owner.close()


@pytest.mark.asyncio
async def test_existing_objects_are_reowned_and_provisioning_is_idempotent(pg_pool, spec):
    async with pg_pool.acquire() as conn:
        # an "existing install": objects owned by the bootstrap role
        await conn.execute('CREATE SCHEMA "ns-old"')
        await conn.execute('CREATE TABLE "ns-old".doc_y__v1 (id SERIAL PRIMARY KEY, v TEXT)')
        await conn.execute('CREATE VIEW "ns-old".doc_y AS SELECT * FROM "ns-old".doc_y__v1')
        await conn.execute("CREATE TABLE IF NOT EXISTS public._wip_sync_status (k TEXT)")

        first = await provision_roles(conn, spec)
        assert first.schemas_reowned >= 1
        assert first.relations_reowned >= 3

        owners = {
            r["relname"]: r["owner"]
            for r in await conn.fetch(
                """
                SELECT c.relname, pg_get_userbyid(c.relowner) AS owner
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE (n.nspname = 'ns-old' AND c.relkind IN ('r', 'v', 'S'))
                   OR (n.nspname = 'public' AND c.relname = '_wip_sync_status')
                """
            )
        }
        assert set(owners.values()) == {spec.owner}, owners
        assert await conn.fetchval(
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'ns-old'"
        ) == spec.owner

        second = await provision_roles(conn, spec)
        assert second.roles_created == []
        assert second.schemas_reowned == 0
        assert second.relations_reowned == 0

    reader = await asyncpg.connect(_dsn_as(spec.reader, spec.reader_password))
    owner = await asyncpg.connect(_dsn_as(spec.owner, spec.owner_password))
    try:
        assert await reader.fetchval('SELECT count(*) FROM "ns-old".doc_y') == 0
        await owner.execute('INSERT INTO "ns-old".doc_y__v1 (v) VALUES (\'x\')')  # sequence too
        await owner.execute('DROP SCHEMA "ns-old" CASCADE')
    finally:
        await reader.close()
        await owner.close()


@pytest.mark.asyncio
async def test_connect_postgres_serves_on_the_service_roles(pg_pool, spec, monkeypatch):
    parts = urlsplit(POSTGRES_TEST_URI)
    s = rs_main.settings
    monkeypatch.setattr(s, "postgres_host", parts.hostname or "localhost")
    monkeypatch.setattr(s, "postgres_port", parts.port or 5432)
    monkeypatch.setattr(s, "postgres_db", parts.path.lstrip("/"))
    monkeypatch.setattr(s, "postgres_user", parts.username)
    monkeypatch.setattr(s, "postgres_password", parts.password)
    monkeypatch.setattr(s, "postgres_owner_user", spec.owner)
    monkeypatch.setattr(s, "postgres_owner_password", spec.owner_password)
    monkeypatch.setattr(s, "postgres_read_user", spec.reader)
    monkeypatch.setattr(s, "postgres_read_password", spec.reader_password)

    owner_pool, read_pool = await rs_main.connect_postgres()
    try:
        async with owner_pool.acquire() as c:
            assert await c.fetchval("SELECT current_user") == spec.owner
        async with read_pool.acquire() as c:
            assert await c.fetchval("SELECT current_user") == spec.reader
            assert await c.fetchval("SHOW default_transaction_read_only") == "on"
    finally:
        await owner_pool.close()
        await read_pool.close()


@pytest.mark.asyncio
async def test_connect_postgres_refuses_without_role_passwords(monkeypatch):
    monkeypatch.setattr(rs_main.settings, "postgres_owner_password", "")
    with pytest.raises(rs_main.ServiceRolesError, match="not set"):
        await rs_main.connect_postgres()


@pytest.mark.asyncio
async def test_health_is_503_when_the_service_roles_are_unusable(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    monkeypatch.setattr(rs_main.state.sync_status, "postgres_service_roles_ok", False)
    transport = ASGITransport(app=rs_main.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/health", "/api/reporting-sync/health"):
            resp = await client.get(path)
            assert resp.status_code == 503, path
            assert resp.json()["status"] == "unhealthy"
