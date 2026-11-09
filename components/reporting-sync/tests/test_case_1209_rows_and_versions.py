"""Rows reach reporting as the store holds them.

- An in-place overwrite (versioned: false) keeps its version and moves its
  revision; the upsert compares the revision, so it lands (latest_only and
  all_versions alike), and an older write never replaces a newer one.
- A document with two active versions in two version tables (a merge
  restore that kept older versions) keeps one live row, its latest write,
  whatever order the versions sync in.
- A field named like a system column gets a data_ prefix in DDL and rows
  alike, so its table is created.
"""

from __future__ import annotations

import pytest

from reporting_sync.main import init_postgres_schema
from reporting_sync.models import ReportingConfig
from reporting_sync.schema_manager import SchemaManager
from reporting_sync.transformer import DocumentTransformer

from .conftest import requires_postgres

pytestmark = [requires_postgres, pytest.mark.asyncio]

NS = "rs1209r"


def _tpl(version: int, strategy: str = "latest_only", extra: list | None = None) -> dict:
    return {
        "template_id": "t-r", "value": "rows", "version": version,
        "fields": [{"name": "k", "type": "string"}, {"name": "v", "type": "string"}] + (extra or []),
        "identity_fields": ["k"], "reporting": {"sync_enabled": True, "sync_strategy": strategy},
    }


def _doc(version: int, value: str, *, revision: int | None = None, template_version: int = 1) -> dict:
    return {"document_id": "d1", "namespace": NS, "template_id": "t-r",
            "template_version": template_version, "version": version, "revision": revision,
            "status": "active", "identity_hash": "h", "created_at": "2026-11-09T10:00:00Z",
            "data": {"k": "k1", "v": value}}


async def _write(pool, template, doc):
    sm = SchemaManager(pool)
    table = await sm.ensure_table_for_template(NS, template, cached=False)
    config = ReportingConfig(**template["reporting"])
    tr = DocumentTransformer(config)
    async with pool.acquire() as conn:
        for row in tr.transform(doc, template):
            sql, params = tr.generate_upsert_sql(table, row, config.sync_strategy.value)
            await conn.execute(sql, *params)
    return table


@pytest.mark.parametrize("strategy", ["latest_only", "all_versions"])
async def test_an_in_place_overwrite_lands_and_an_older_write_does_not(pg_pool, strategy):
    await init_postgres_schema(pg_pool)
    tpl = _tpl(1, strategy)
    table = await _write(pg_pool, tpl, _doc(1, "first"))
    await _write(pg_pool, tpl, _doc(1, "second", revision=2))  # versioned:false overwrite
    await _write(pg_pool, tpl, _doc(1, "stale", revision=1))  # a late redelivery
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(f'SELECT v FROM {table}')
    assert [r["v"] for r in rows] == ["second"]


@pytest.mark.parametrize("order", [(1, 2), (2, 1)])
async def test_two_active_versions_keep_the_latest_write(pg_pool, order):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    docs = {1: _doc(1, "old", template_version=1), 2: _doc(2, "new", template_version=2)}
    for v in order:
        await _write(pg_pool, _tpl(v), docs[v])
        await sm.delete_from_sibling_version_tables(
            NS, "rows", None, keep_version=v, document_id="d1", keep_revision=docs[v]["version"],
        )
    async with pg_pool.acquire() as conn:
        v1 = await conn.fetch(f'SELECT v FROM "{NS}"."doc_rows__v1"')
        v2 = await conn.fetch(f'SELECT v FROM "{NS}"."doc_rows__v2"')
    assert ([r["v"] for r in v1], [r["v"] for r in v2]) == ([], ["new"])


@pytest.mark.parametrize("name", ["namespace", "file_references_json", "_wip_revision"])
async def test_a_field_named_like_a_system_column_gets_its_table(pg_pool, name):
    await init_postgres_schema(pg_pool)
    tpl = _tpl(1, extra=[{"name": name, "type": "string"}])
    tpl["value"] = f"sys_{name.strip('_')}"
    doc = _doc(1, "x")
    doc["data"][name] = "field value"
    table = await _write(pg_pool, tpl, doc)
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(f'SELECT * FROM {table}')
    assert row[f"data_{name}"] == "field value"


async def test_an_undeprecated_term_loses_its_deprecation(pg_pool):
    """Only the dedicated deprecation event used to write deprecated_reason /
    replaced_by_term_id, so a term brought back kept a stale reason."""
    from unittest.mock import MagicMock

    from reporting_sync.models import SyncStatus
    from reporting_sync.worker import SyncWorker

    await init_postgres_schema(pg_pool)
    w = SyncWorker(MagicMock(), MagicMock(), pg_pool,
                   SyncStatus(running=True, connected_to_nats=True, connected_to_postgres=True))
    w.closure.mark_dirty = MagicMock()
    term = {"term_id": "t1", "namespace": NS, "terminology_id": "tt", "value": "a",
            "status": "deprecated", "deprecated_reason": "merged", "replaced_by_term_id": "t2"}
    await w._process_term_event({"event_type": "term.updated", "term": term})
    term = {**term, "status": "active", "deprecated_reason": None, "replaced_by_term_id": None}
    await w._process_term_event({"event_type": "term.updated", "term": term})
    table = await w.schema_manager.ensure_terms_table(NS)
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow(f'SELECT status, deprecated_reason, replaced_by_term_id FROM {table}')
    assert tuple(row) == ("active", None, None)
