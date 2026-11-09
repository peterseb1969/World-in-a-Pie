"""A terminologies table created before `extensible` was removed loses it.

def-store no longer has the field, so reporting-sync no longer writes it. A
table created with the old DDL has the column dropped the first time the
table is ensured; a new table is created without it.
"""

import pytest

from reporting_sync.schema_manager import SchemaManager

from .conftest import requires_postgres

NS = "probe1182"


async def _columns(pool, schema):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = 'terminologies'", schema,
        )
    return {r["column_name"] for r in rows}


@requires_postgres
@pytest.mark.asyncio
async def test_old_table_loses_the_column_and_new_ones_never_have_it(pg_pool):
    async def active(_ns):
        return "active"

    sm = SchemaManager(pg_pool, namespace_status=active)
    table = await sm.ensure_terminologies_table(NS)
    schema = table.split(".")[0].strip('"')
    assert "extensible" not in await _columns(pg_pool, schema)

    async with pg_pool.acquire() as conn:
        await conn.execute(f'ALTER TABLE {table} ADD COLUMN "extensible" BOOLEAN DEFAULT TRUE')  # nosec B608
    fresh = SchemaManager(pg_pool, namespace_status=active)  # a new process
    await fresh.ensure_terminologies_table(NS)

    assert "extensible" not in await _columns(pg_pool, schema)
