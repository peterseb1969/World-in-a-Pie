"""Per-event catalog queries for an existing table (CASE-868).

``ensure_table_for_template`` runs on every synced document event. For a
table that already exists it used to ask the catalog whether the table
exists twice (once itself, once again at the top of
``update_table_schema``), and for identity-less templates it also looked up
a legacy unique-identity index on every event, although that index can
never come back once checked. One existence check is now made per event,
and the legacy-index lookup runs once per table per process.
"""

import asyncpg
import pytest

from reporting_sync.main import init_postgres_schema
from reporting_sync.schema_manager import SchemaManager

from .conftest import requires_postgres

NS = "ns-868"
CATALOGS = ("information_schema.tables", "information_schema.columns", "pg_indexes")


def _template(value: str, identity_fields: list[str]) -> dict:
    return {
        "value": value,
        "version": 1,
        "fields": [{"name": "title", "type": "string"}],
        "identity_fields": identity_fields,
        "reporting": {"sync_enabled": True},
    }


@pytest.fixture
def catalog_queries(monkeypatch):
    """Records every fetch/fetchval/fetchrow statement that reads a catalog."""
    seen: list[str] = []
    for method in ("fetch", "fetchval", "fetchrow"):
        original = getattr(asyncpg.connection.Connection, method)

        def wrapper(self, query, *args, _original=original, **kwargs):
            if any(c in query for c in CATALOGS):
                seen.append(" ".join(query.split()))
            return _original(self, query, *args, **kwargs)

        monkeypatch.setattr(asyncpg.connection.Connection, method, wrapper)
    return seen


def _count(queries: list[str], catalog: str) -> int:
    return sum(catalog in q for q in queries)


@requires_postgres
@pytest.mark.asyncio
async def test_an_existing_table_is_looked_up_once_per_event(pg_pool, catalog_queries):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    await sm.ensure_table_for_template(NS, _template("keyed", ["title"]))

    catalog_queries.clear()
    await sm.ensure_table_for_template(NS, _template("keyed", ["title"]))

    assert _count(catalog_queries, "information_schema.tables") == 1
    assert _count(catalog_queries, "information_schema.columns") == 1
    assert _count(catalog_queries, "pg_indexes") == 0


@requires_postgres
@pytest.mark.asyncio
async def test_the_legacy_index_check_runs_once_per_table(pg_pool, catalog_queries):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    await sm.ensure_table_for_template(NS, _template("append_only", []))

    catalog_queries.clear()
    for _ in range(3):
        await sm.ensure_table_for_template(NS, _template("append_only", []))

    # The first ensure above created the table, so the first re-ensure is
    # the table's first migration pass and checks for the index once.
    assert _count(catalog_queries, "pg_indexes") == 1
    assert _count(catalog_queries, "information_schema.tables") == 3


@requires_postgres
@pytest.mark.asyncio
async def test_update_table_schema_still_creates_a_missing_table(pg_pool):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    fields = SchemaManager.parse_template_fields(_template("late", [])["fields"])

    migrations = await sm.update_table_schema(NS, "late", 1, fields, identity_fields=[])

    assert migrations == ["Created table doc_late__v1"]
    assert await sm.table_exists(NS, "doc_late__v1")
