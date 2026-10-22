"""The ensured-table cache (CASE-868).

A template version's reporting table never changes shape, so once
``ensure_table_for_template`` has ensured it, document events skip the
catalog checks — measured at about 40% of a document event's handling time.
The cache is shared by every SchemaManager on a pool; reporting-sync's own
drops evict it, and a write that meets a table changed behind its back
re-ensures and retries once.
"""

from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from reporting_sync.main import init_postgres_schema
from reporting_sync.models import SyncStatus
from reporting_sync.schema_manager import SchemaManager
from reporting_sync.worker import SyncWorker

from .conftest import requires_postgres
from .test_worker import _make_document_event, _make_template

NS = "ns-868c"
CATALOGS = ("information_schema.tables", "information_schema.columns", "pg_indexes")


async def _active(namespace):
    return "active"


@pytest.fixture
def catalog_queries(monkeypatch):
    """Records every fetch/fetchval/fetchrow statement that reads a catalog."""
    seen: list[str] = []
    for method in ("fetch", "fetchval", "fetchrow"):
        original = getattr(asyncpg.connection.Connection, method)

        def wrapper(self, query, *args, _original=original, **kwargs):
            if any(c in query for c in CATALOGS):
                seen.append(query)
            return _original(self, query, *args, **kwargs)

        monkeypatch.setattr(asyncpg.connection.Connection, method, wrapper)
    return seen


def _template(value: str = "note", version: int = 1) -> dict:
    return _make_template(
        template_id=f"tpl-{value}",
        value=value,
        version=version,
        fields=[{"name": "title", "type": "string"}],
        reporting={"sync_enabled": True},
        namespace=NS,
    )


async def _table_exists(pool, table: str) -> bool:
    return bool(await pool.fetchval(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = $1 AND table_name = $2",
        NS, table,
    ))


@requires_postgres
@pytest.mark.asyncio
async def test_a_cached_ensure_reads_no_catalog(pg_pool, catalog_queries):
    await init_postgres_schema(pg_pool)
    first = await SchemaManager(pg_pool).ensure_table_for_template(NS, _template())

    catalog_queries.clear()
    # A different SchemaManager on the same pool shares the cache.
    again = await SchemaManager(pg_pool).ensure_table_for_template(NS, _template())

    assert again == first == f'"{NS}"."doc_note__v1"'
    assert catalog_queries == []


@requires_postgres
@pytest.mark.asyncio
async def test_a_new_template_version_is_ensured_on_first_use(pg_pool):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    await sm.ensure_table_for_template(NS, _template(version=1))
    await sm.ensure_table_for_template(NS, _template(version=2))
    assert await _table_exists(pg_pool, "doc_note__v2")


@requires_postgres
@pytest.mark.asyncio
async def test_namespace_deletion_evicts(pg_pool):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool, namespace_status=_active)
    await sm.ensure_table_for_template(NS, _template())
    # Another instance drops, as the DELETE endpoint does.
    await SchemaManager(pg_pool, namespace_status=_active).drop_namespace_schema(NS)

    # The namespace is active again (re-created), so the table comes back.
    await sm.ensure_table_for_template(NS, _template())
    assert await _table_exists(pg_pool, "doc_note__v1")


@requires_postgres
@pytest.mark.asyncio
async def test_force_rebuild_evicts_only_that_template(pg_pool, catalog_queries):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    await sm.ensure_table_for_template(NS, _template("note"))
    await sm.ensure_table_for_template(NS, _template("notebook"))
    await sm.drop_relations_for_template(NS, "note")

    await sm.ensure_table_for_template(NS, _template("note"))
    assert await _table_exists(pg_pool, "doc_note__v1")

    # "notebook" shares the "note" prefix but is a different template: still cached.
    catalog_queries.clear()
    await sm.ensure_table_for_template(NS, _template("notebook"))
    assert catalog_queries == []


@requires_postgres
@pytest.mark.asyncio
async def test_a_table_dropped_behind_the_cache_heals_on_the_next_write(pg_pool):
    await init_postgres_schema(pg_pool)
    status = SyncStatus(running=True, connected_to_nats=True, connected_to_postgres=True)
    worker = SyncWorker(MagicMock(), MagicMock(), pg_pool, status)
    template = _template()
    worker._fetch_template = AsyncMock(return_value=template)

    def event(document_id: str, title: str) -> dict:
        e = _make_document_event(document_id=document_id, template_id=template["template_id"],
                                 data={"title": title})
        e["document"]["namespace"] = NS
        return e

    assert await worker._process_document_event(event("d1", "first"))
    # Dropped outside reporting-sync: the cache still says it exists.
    await pg_pool.execute(f'DROP TABLE "{NS}"."doc_note__v1" CASCADE')

    assert await worker._process_document_event(event("d2", "second"))
    titles = await pg_pool.fetch(f'SELECT title FROM "{NS}"."doc_note__v1"')
    assert [r["title"] for r in titles] == ["second"]
