"""A table's search hits come back in a stable order.

The search orchestrator re-runs the whole search for every page and slices
the merged list, so each table's hits must come back in the same order on
every run. Without an ORDER BY (substring path) or a tie-breaker after the
score (FTS path), PostgreSQL returns rows in physical order, and a row the
sync rewrites moves: paging then repeats some hits and never shows others
(reproduced live: 68 hits paged 20 at a time came back as 63–66 distinct).
"""

import pytest

from reporting_sync.search_service import SearchService

from .conftest import requires_postgres

pytestmark = [pytest.mark.asyncio, requires_postgres]


async def _table(pg_pool):
    async with pg_pool.acquire() as conn:
        await conn.execute('CREATE SCHEMA IF NOT EXISTS "ss"')
        await conn.execute('''
            CREATE TABLE "ss"."doc_note__v1" (
                document_id TEXT PRIMARY KEY, namespace TEXT, status TEXT,
                updated_at TIMESTAMPTZ, title TEXT, title_search TEXT,
                title_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', coalesce(title_search, ''))) STORED
            )''')
        for i in range(1, 6):
            await conn.execute(
                'INSERT INTO "ss"."doc_note__v1" VALUES ($1, $2, $3, NULL, $4, $4)',
                f"d{i}", "ss", "active", "chicken soup",
            )
        # Rewriting a row moves it to the end of the heap, as a re-sync does.
        await conn.execute('UPDATE "ss"."doc_note__v1" SET title = title WHERE document_id = $1', "d1")
        await conn.execute('UPDATE "ss"."doc_note__v1" SET title_search = title_search WHERE document_id = $1', "d3")


async def test_substring_hits_are_ordered_by_document(pg_pool):
    await _table(pg_pool)
    svc = SearchService(pg_pool)
    async with pg_pool.acquire() as conn:
        rows = await svc._substring_query(
            conn, "ss", "doc_note__v1", ["document_id", "status", "title"], "chicken",
            None, False, 100,
        )
    assert [r["doc_id"] for r in rows] == ["d1", "d2", "d3", "d4", "d5"]


async def test_fts_ties_are_ordered_by_document(pg_pool):
    await _table(pg_pool)
    svc = SearchService(pg_pool)
    async with pg_pool.acquire() as conn:
        rows = await svc._fts_query(
            conn, "ss", "doc_note__v1", ["title_tsv"], "chicken", None, False, "text", 100,
        )
    assert len({r["score"] for r in rows}) == 1
    assert [r["doc_id"] for r in rows] == ["d1", "d2", "d3", "d4", "d5"]
