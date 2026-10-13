"""An unchanged full-text index is not a migration (CASE-867).

``update_table_schema`` runs on every synced document event. It used to
execute and record ``CREATE INDEX IF NOT EXISTS … _tsv_idx`` every time, so
the template's migration row grew by one statement per event (megabytes on
large loads), each append rewrote the whole value, and the no-op still took
a SHARE lock on the table. Only an index that is actually missing is now
created and recorded; existing bloated rows are compacted at startup.
"""

import pytest

from reporting_sync.bookkeeping import compact_fts_index_lines, compact_migration_journal
from reporting_sync.main import init_postgres_schema
from reporting_sync.schema_manager import SchemaManager

from .conftest import requires_postgres

FTS_IDX = 'CREATE INDEX IF NOT EXISTS "doc_x__v1_body_tsv_idx" ON "ns"."doc_x__v1" USING GIN ("body_tsv")'


# ── pure functions ────────────────────────────────────────────────────────


def test_compaction_keeps_the_first_fts_index_statement_only():
    sql = "\n".join([
        'CREATE TABLE "ns"."doc_x__v1" (',
        "    id TEXT",
        ");",
        FTS_IDX + ";",
        FTS_IDX,
        FTS_IDX,
    ])
    assert compact_fts_index_lines(sql) == "\n".join([
        'CREATE TABLE "ns"."doc_x__v1" (', "    id TEXT", ");", FTS_IDX + ";",
    ])


def test_compaction_leaves_other_repeated_lines_alone():
    sql = ");\n);\nALTER TABLE t ADD COLUMN a TEXT\nALTER TABLE t ADD COLUMN a TEXT"
    assert compact_fts_index_lines(sql) == sql


def test_compaction_is_idempotent():
    once = compact_fts_index_lines("\n".join([FTS_IDX] * 5))
    assert compact_fts_index_lines(once) == once == FTS_IDX


def test_stored_identifier_matches_postgres_truncation():
    long = "doc_" + "a" * 80 + "_body_tsv_idx"
    assert SchemaManager._stored_identifier(long) == long[:63]
    assert SchemaManager._stored_identifier("short_idx") == "short_idx"


# ── real PostgreSQL ───────────────────────────────────────────────────────


def _template(value: str = "lesson") -> dict:
    return {
        "value": value,
        "version": 1,
        "fields": [
            {"name": "title", "type": "string"},
            {"name": "body", "type": "string", "full_text_indexed": True},
        ],
        "identity_fields": ["title"],
        "reporting": {"sync_enabled": True},
    }


async def _migration_row(pool, template_value: str) -> str:
    return await pool.fetchval(
        "SELECT migration_sql FROM public._wip_schema_migrations "
        "WHERE namespace = 'ns-fts' AND template_value = $1",
        template_value,
    )


@requires_postgres
@pytest.mark.asyncio
async def test_repeated_ensure_records_nothing(pg_pool):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    await sm.ensure_table_for_template("ns-fts", _template())
    created = await _migration_row(pg_pool, "lesson")

    fields = SchemaManager.parse_template_fields(_template()["fields"])
    for _ in range(3):
        migrations = await sm.update_table_schema(
            "ns-fts", "lesson", 1, fields, identity_fields=["title"]
        )
        assert migrations == []
        await sm.ensure_table_for_template("ns-fts", _template())

    assert await _migration_row(pg_pool, "lesson") == created


@requires_postgres
@pytest.mark.asyncio
async def test_a_missing_fts_index_is_created_and_recorded_once(pg_pool):
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    await sm.ensure_table_for_template("ns-fts", _template())
    await pg_pool.execute('DROP INDEX "ns-fts"."doc_lesson__v1_body_tsv_idx"')

    fields = SchemaManager.parse_template_fields(_template()["fields"])
    first = await sm.update_table_schema("ns-fts", "lesson", 1, fields, identity_fields=["title"])
    assert len(first) == 1 and "doc_lesson__v1_body_tsv_idx" in first[0]
    assert await pg_pool.fetchval(
        "SELECT 1 FROM pg_indexes WHERE schemaname = 'ns-fts' "
        "AND indexname = 'doc_lesson__v1_body_tsv_idx'"
    )
    second = await sm.update_table_schema("ns-fts", "lesson", 1, fields, identity_fields=["title"])
    assert second == []


@requires_postgres
@pytest.mark.asyncio
async def test_a_truncated_index_name_is_recognised(pg_pool):
    """Index names over 63 bytes are stored truncated; the existence check
    must compare against the stored name or it would re-create forever."""
    await init_postgres_schema(pg_pool)
    sm = SchemaManager(pg_pool)
    value = "a_rather_long_template_value_for_truncation"
    await sm.ensure_table_for_template("ns-fts", _template(value))
    created = await _migration_row(pg_pool, value)

    fields = SchemaManager.parse_template_fields(_template(value)["fields"])
    assert await sm.update_table_schema(
        "ns-fts", value, 1, fields, identity_fields=["title"]
    ) == []
    assert await _migration_row(pg_pool, value) == created


@requires_postgres
@pytest.mark.asyncio
async def test_journal_compaction_rewrites_only_bloated_rows(pg_pool):
    await init_postgres_schema(pg_pool)
    bloated = "\n".join(["CREATE TABLE t (", "  id TEXT", ");", FTS_IDX + ";"] + [FTS_IDX] * 50)
    clean = "CREATE TABLE u (id TEXT);\n" + FTS_IDX
    await pg_pool.executemany(
        "INSERT INTO public._wip_schema_migrations "
        "(namespace, template_value, template_version, migration_sql) VALUES ($1, $2, 1, $3)",
        [("ns-fts", "bloated", bloated), ("ns-fts", "clean", clean)],
    )
    async with pg_pool.acquire() as conn:
        assert await compact_migration_journal(conn) == 1
        assert await compact_migration_journal(conn) == 0

    assert await _migration_row(pg_pool, "bloated") == compact_fts_index_lines(bloated)
    assert (await _migration_row(pg_pool, "bloated")).count("_tsv_idx") == 1
    assert await _migration_row(pg_pool, "clean") == clean
