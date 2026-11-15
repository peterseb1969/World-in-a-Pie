"""Adding a template version keeps an integer field's column in the entity views.

An older install's per-version tables hold integer-field columns as int4,
created before integer mapped to int8. A new version table is created as
bigint, and a view keeps a column only at the type every version table
shares: without widening the siblings, the column left the view while its
data stayed in the tables, and every query naming it failed.
"""

from __future__ import annotations

import pytest

from reporting_sync.main import init_postgres_schema
from reporting_sync.schema_manager import SchemaManager

from .conftest import requires_postgres

NS = "rs1311"


def _tpl(version: int, extra: str | None = None) -> dict:
    fields = [
        {"name": "k", "type": "string", "mandatory": True},
        {"name": "n", "type": "integer", "mandatory": True},
    ]
    if extra:
        fields.append({"name": extra, "type": "string"})
    return {
        "template_id": "t-1311",
        "value": "counts",
        "version": version,
        "fields": fields,
        "identity_fields": ["k"],
        "reporting": {"sync_enabled": True},
    }


async def _column_types(pool, column: str) -> dict[str, str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT table_name, data_type FROM information_schema.columns "
            "WHERE table_schema = $1 AND column_name = $2",
            NS,
            column,
        )
    return {r["table_name"]: r["data_type"] for r in rows}


async def _make_legacy_int4(pool, versions: list[int]) -> None:
    """Tables as an older reporting-sync left them: int4 integer columns."""
    async with pool.acquire() as conn:
        await conn.execute(
            f'DROP VIEW IF EXISTS "{NS}"."doc_counts", "{NS}"."doc_counts__entities"'
        )
        for v in versions:
            await conn.execute(
                f'ALTER TABLE "{NS}"."doc_counts__v{v}" ALTER COLUMN "n" TYPE INTEGER'
            )


@requires_postgres
@pytest.mark.asyncio
async def test_new_version_table_widens_int4_siblings(pg_pool):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)

    await sm.ensure_table_for_template(NS, _tpl(1))
    await sm.ensure_table_for_template(NS, _tpl(2))
    await _make_legacy_int4(pg_pool, [1, 2])

    # v3 is a new table: creating it must not leave the int4 siblings behind.
    await sm.ensure_table_for_template(NS, _tpl(3, extra="later"))

    types = await _column_types(pg_pool, "n")
    assert types == {
        "doc_counts__v1": "bigint",
        "doc_counts__v2": "bigint",
        "doc_counts__v3": "bigint",
        "doc_counts": "bigint",
        "doc_counts__entities": "bigint",
    }


@requires_postgres
@pytest.mark.asyncio
async def test_view_keeps_rows_written_before_the_widening(pg_pool):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)

    await sm.ensure_table_for_template(NS, _tpl(1))
    await _make_legacy_int4(pg_pool, [1])
    await sm.ensure_table_for_template(NS, _tpl(2, extra="later"))

    async with pg_pool.acquire() as conn:
        # A row written after v3 appeared, into the widened sibling table,
        # reads through the entity view — past int4's 2^31 ceiling.
        await conn.execute(
            f'INSERT INTO "{NS}"."doc_counts__v1" (document_id, namespace, '
            "template_id, template_version, version, status, identity_hash, "
            "created_at, k, n) "
            "VALUES ('d-1', $1, 't-1311', 1, 1, 'active', 'h1', now(), 'a', $2)",
            NS,
            3_000_000_000,
        )
        await conn.execute(
            f'INSERT INTO "{NS}"."doc_counts__v2" (document_id, namespace, '
            "template_id, template_version, version, status, identity_hash, "
            "created_at, k, n) "
            "VALUES ('d-2', $1, 't-1311', 2, 1, 'active', 'h2', now(), 'b', $2)",
            NS,
            7,
        )
        rows = await conn.fetch(
            f'SELECT document_id, n FROM "{NS}"."doc_counts" ORDER BY document_id'
        )
    assert [(r["document_id"], r["n"]) for r in rows] == [("d-1", 3_000_000_000), ("d-2", 7)]


@requires_postgres
@pytest.mark.asyncio
async def test_first_version_still_gets_its_views(pg_pool):
    """Nothing to widen on a fresh entity: the views are built regardless."""
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)

    await sm.ensure_table_for_template(NS, _tpl(1))

    assert await sm.relation_kind(NS, "doc_counts") == "view"
    assert await sm.relation_kind(NS, "doc_counts__entities") == "view"
    assert await _column_types(pg_pool, "n") == {
        "doc_counts__v1": "bigint",
        "doc_counts": "bigint",
        "doc_counts__entities": "bigint",
    }
