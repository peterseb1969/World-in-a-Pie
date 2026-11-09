"""The deleted-namespaces table survives its concurrent first create.

Several first callers of ensure_schema in a fresh process each create
public._wip_deleted_namespaces with CREATE TABLE IF NOT EXISTS. That is not
concurrency-safe: losers can collide on the catalog, and PostgreSQL reports
the collision on the table's row type as DuplicateObjectError ("type ...
already exists") as well as on the relation. The table exists all the same,
so losing must mean "someone else created it", never a failed sync — a
restore fans out per-template syncs that all ensure their namespace at once.
"""

import asyncio

import asyncpg
import pytest

from reporting_sync.schema_manager import SchemaManager

from .conftest import POSTGRES_TEST_URI

pytestmark = pytest.mark.asyncio


async def test_concurrent_first_creates_of_the_tombstone_table_never_fail(pg_pool):
    # One connection per caller, so the creates really run at once.
    conns = [await asyncpg.connect(POSTGRES_TEST_URI) for _ in range(12)]
    try:
        for round_ in range(300):
            await conns[0].execute("DROP TABLE IF EXISTS public._wip_deleted_namespaces")
            results = await asyncio.gather(
                # A fresh SchemaManager each: every caller is a first creator.
                *(SchemaManager(pg_pool)._ensure_tombstone_table(c) for c in conns),
                return_exceptions=True,
            )
            errors = [r for r in results if isinstance(r, Exception)]
            assert not errors, f"round {round_}: {errors[:3]}"
    finally:
        for c in conns:
            await c.close()
    async with pg_pool.acquire() as conn:
        assert await conn.fetchval("SELECT to_regclass('public._wip_deleted_namespaces') IS NOT NULL")

