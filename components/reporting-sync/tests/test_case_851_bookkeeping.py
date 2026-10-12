"""Bookkeeping tables live in public, and an existing split is repaired
(CASE-851, Phase 2b C7).

The test database logs in as ``test``, so a schema named ``test`` plays the
part a ``wip`` namespace schema plays on a real install: the schema that
search_path's ``"$user"`` entry resolves to.
"""

import pytest

from reporting_sync.bookkeeping import repair_bookkeeping_location
from reporting_sync.main import init_postgres_schema

from .conftest import requires_postgres

pytestmark = requires_postgres

STATUS_DDL = """
    CREATE TABLE {schema}._wip_sync_status (
        namespace VARCHAR(255) NOT NULL DEFAULT 'wip',
        template_value TEXT NOT NULL,
        last_sync_at TIMESTAMP WITH TIME ZONE,
        documents_synced BIGINT DEFAULT 0,
        last_error TEXT,
        last_error_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (namespace, template_value)
    )
"""
LEGACY_STATUS_DDL = """
    CREATE TABLE {schema}._wip_sync_status (
        template_value TEXT PRIMARY KEY,
        documents_synced BIGINT DEFAULT 0
    )
"""


async def _role_schema(conn) -> str:
    role = await conn.fetchval("SELECT current_user")
    await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{role}"')
    return role


async def _tables_named(conn, name: str) -> list[tuple[str, str]]:
    rows = await conn.fetch(
        """
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_name LIKE $1 ORDER BY 1, 2
        """,
        f"{name}%",
    )
    return [(r["table_schema"], r["table_name"]) for r in rows]


@pytest.mark.asyncio
async def test_role_schema_copy_alone_is_moved_to_public(pg_pool):
    async with pg_pool.acquire() as conn:
        role = await _role_schema(conn)
        await conn.execute(STATUS_DDL.format(schema=f'"{role}"'))
        await conn.execute(
            f'INSERT INTO "{role}"._wip_sync_status (namespace, template_value, documents_synced) '
            "VALUES ('ns-a', 'PATIENT', 7)"
        )

        actions = await repair_bookkeeping_location(conn)

        assert any("moved" in a for a in actions)
        assert await _tables_named(conn, "_wip_sync_status") == [("public", "_wip_sync_status")]
        assert await conn.fetchval(
            "SELECT documents_synced FROM public._wip_sync_status "
            "WHERE namespace = 'ns-a' AND template_value = 'PATIENT'"
        ) == 7


@pytest.mark.asyncio
async def test_both_copies_merge_and_the_role_schema_copy_wins(pg_pool):
    async with pg_pool.acquire() as conn:
        role = await _role_schema(conn)
        for schema in ("public", f'"{role}"'):
            await conn.execute(STATUS_DDL.format(schema=schema))
        # stale row in public, live row for the same key in the role schema
        await conn.execute(
            "INSERT INTO public._wip_sync_status (namespace, template_value, documents_synced) "
            "VALUES ('ns-a', 'PATIENT', 1), ('ns-a', 'ONLY_PUBLIC', 3)"
        )
        await conn.execute(
            f'INSERT INTO "{role}"._wip_sync_status (namespace, template_value, documents_synced) '
            "VALUES ('ns-a', 'PATIENT', 9), ('ns-b', 'ONLY_SHADOW', 4)"
        )

        actions = await repair_bookkeeping_location(conn)

        assert any("merged" in a for a in actions)
        assert await _tables_named(conn, "_wip_sync_status") == [("public", "_wip_sync_status")]
        rows = {
            (r["namespace"], r["template_value"]): r["documents_synced"]
            for r in await conn.fetch("SELECT * FROM public._wip_sync_status")
        }
        assert rows == {
            ("ns-a", "PATIENT"): 9,  # the live (role-schema) value won
            ("ns-a", "ONLY_PUBLIC"): 3,
            ("ns-b", "ONLY_SHADOW"): 4,
        }


@pytest.mark.asyncio
async def test_pre_namespace_public_copy_is_renamed_aside(pg_pool):
    async with pg_pool.acquire() as conn:
        role = await _role_schema(conn)
        await conn.execute(LEGACY_STATUS_DDL.format(schema="public"))
        await conn.execute(STATUS_DDL.format(schema=f'"{role}"'))

        actions = await repair_bookkeeping_location(conn)

        assert any("pre-namespace" in a for a in actions)
        tables = await _tables_named(conn, "_wip_sync_status")
        assert ("public", "_wip_sync_status") in tables
        assert any(t.startswith("_wip_sync_status__legacy_") for _, t in tables)
        assert not any(s == role for s, _ in tables)
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = '_wip_sync_status'"
        )
        assert "namespace" in {c["column_name"] for c in cols}


@pytest.mark.asyncio
async def test_unexpected_shape_is_left_aside_not_merged(pg_pool):
    async with pg_pool.acquire() as conn:
        role = await _role_schema(conn)
        await conn.execute(STATUS_DDL.format(schema="public"))
        await conn.execute(LEGACY_STATUS_DDL.format(schema=f'"{role}"'))

        actions = await repair_bookkeeping_location(conn)

        assert any("do not match" in a for a in actions)
        tables = await _tables_named(conn, "_wip_sync_status")
        assert ("public", "_wip_sync_status") in tables
        assert any(s == role and t.startswith("_wip_sync_status__shadow_") for s, t in tables)


@pytest.mark.asyncio
async def test_repair_is_a_no_op_without_a_role_schema_copy(pg_pool):
    async with pg_pool.acquire() as conn:
        role = await _role_schema(conn)
        await conn.execute(STATUS_DDL.format(schema=f'"{role}"'))
        assert await repair_bookkeeping_location(conn)
        assert await repair_bookkeeping_location(conn) == []


@pytest.mark.asyncio
async def test_init_creates_bookkeeping_in_public_even_with_a_role_schema(pg_pool):
    """The original split: with a schema named after the login role, an
    unqualified CREATE TABLE landed there. Qualified, it cannot."""
    async with pg_pool.acquire() as conn:
        role = await _role_schema(conn)
    await init_postgres_schema(pg_pool)
    async with pg_pool.acquire() as conn:
        for table in ("_wip_schema_migrations", "_wip_sync_status", "_wip_views"):
            assert await _tables_named(conn, table) == [("public", table)], table
        assert role  # the role schema existed throughout
