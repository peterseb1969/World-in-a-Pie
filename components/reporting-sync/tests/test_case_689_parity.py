"""Parity check: does postgres reflect what sync should have built?

Unit half: the expected-column oracle must derive exactly what the schema
manager builds (shared _generate_column_ddl rules — term double-columns,
SYSTEM_COLUMNS prefixing, FTS pairs, relationship extras). Postgres half:
namespace-level detection — a pre-namespace-keying bookkeeping table is
reported as unusable (the silent-empty-reporting incident class), and a
correctly built table verifies structurally green.
"""

from unittest.mock import AsyncMock, patch

import pytest

from reporting_sync.models import FieldType, ReportingConfig
from reporting_sync.parity import check_namespace_parity
from reporting_sync.schema_manager import SchemaManager

from .conftest import requires_postgres


# ---------------------------------------------------------------------------
# Unit: the expected-column oracle
# ---------------------------------------------------------------------------


class TestExpectedColumns:
    def _sm(self) -> SchemaManager:
        return SchemaManager(pool=None)  # type: ignore[arg-type] — pure derivation, no I/O

    def test_string_and_term_fields(self):
        fields = SchemaManager.parse_template_fields([
            {"name": "title", "type": "string"},
            {"name": "category", "type": "term", "terminology_ref": "CATEGORY"},
            # `status` collides with a system column → whole field prefixed,
            # companion column included
            {"name": "status", "type": "term", "terminology_ref": "STATUS"},
        ])
        cols = self._sm().expected_columns_for_template(fields)
        assert "title" in cols
        # term fields produce the value column plus its _term_id companion
        assert {"category", "category_term_id"} <= cols
        assert {"data_status", "data_status_term_id"} <= cols
        assert "status" not in cols

    def test_system_column_collision_gets_data_prefix(self):
        fields = SchemaManager.parse_template_fields([
            {"name": "version", "type": "string"},
        ])
        cols = self._sm().expected_columns_for_template(fields)
        assert "data_version" in cols
        assert "version" not in cols

    def test_full_text_indexed_adds_fts_pair(self):
        fields = SchemaManager.parse_template_fields([
            {"name": "body", "type": "string", "full_text_indexed": True},
        ])
        cols = self._sm().expected_columns_for_template(fields)
        assert {"body", "body_search", "body_tsv"} <= cols

    def test_relationship_usage_adds_ref_id_columns(self):
        cols = self._sm().expected_columns_for_template([], usage="relationship")
        assert cols == {"source_ref_id", "target_ref_id"}


# ---------------------------------------------------------------------------
# Postgres: namespace-level detection + structural verification
# ---------------------------------------------------------------------------

TEMPLATE = {
    "id": "tpl-1",
    "value": "PARITY_T",
    "usage": "entity",
    "fields": [{"name": "title", "type": "string"}],
    "reporting": {},
}


def _patch_http(templates: list[dict], doc_total: int = 0):
    """Patch parity's two HTTP fetches — template-store and document-store."""
    return (
        patch(
            "reporting_sync.parity._fetch_namespace_templates",
            new=AsyncMock(return_value=templates),
        ),
        patch(
            "reporting_sync.parity._expected_document_total",
            new=AsyncMock(return_value=doc_total),
        ),
    )


@requires_postgres
@pytest.mark.asyncio
async def test_old_shape_bookkeeping_reported_unusable(pg_pool):
    """A bookkeeping table without the namespace column must be named
    loudly at namespace level — not surface as per-type sync failures."""
    async with pg_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE _wip_schema_migrations (
                template_value TEXT NOT NULL,
                template_version INTEGER NOT NULL,
                migration_sql TEXT NOT NULL,
                PRIMARY KEY (template_value, template_version)
            )
        """)
    p_t, p_d = _patch_http([])
    with p_t, p_d:
        result = await check_namespace_parity(pg_pool, "parityns")
    assert result.bookkeeping_tables_ok is False
    assert "namespace" in (result.bookkeeping_error or "")
    assert result.ok is False


@requires_postgres
@pytest.mark.asyncio
async def test_fresh_database_structurally_green(pg_pool):
    """New-shape bookkeeping + a table built by the schema manager itself
    must verify green — the oracle agrees with what sync builds."""
    async with pg_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE _wip_schema_migrations (
                namespace VARCHAR(255) NOT NULL DEFAULT 'wip',
                template_value TEXT NOT NULL,
                template_version INTEGER NOT NULL,
                migration_sql TEXT NOT NULL,
                PRIMARY KEY (namespace, template_value, template_version)
            )
        """)

    sm = SchemaManager(pg_pool)
    fields = SchemaManager.parse_template_fields(TEMPLATE["fields"])
    await sm.create_table("parityns", "PARITY_T", 1, fields, ReportingConfig())

    p_t, p_d = _patch_http([TEMPLATE], doc_total=0)
    with p_t, p_d:
        result = await check_namespace_parity(pg_pool, "parityns", include_counts=True)

    assert result.schema_present is True
    assert result.bookkeeping_tables_ok is True
    assert result.structural_issues == 0
    row = result.templates[0]
    assert row.table_present is True
    assert row.missing_columns == []
    assert row.counts_match is True
    assert result.ok is True


@requires_postgres
@pytest.mark.asyncio
async def test_missing_table_is_a_structural_issue(pg_pool):
    """A sync-enabled template with no table must count as a structural
    issue — the exact restore phase-1 red condition."""
    async with pg_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE _wip_schema_migrations (
                namespace VARCHAR(255) NOT NULL DEFAULT 'wip',
                template_value TEXT NOT NULL,
                template_version INTEGER NOT NULL,
                migration_sql TEXT NOT NULL,
                PRIMARY KEY (namespace, template_value, template_version)
            )
        """)
    p_t, p_d = _patch_http([TEMPLATE])
    with p_t, p_d:
        result = await check_namespace_parity(pg_pool, "parityns", include_counts=False)
    assert result.structural_issues == 1
    assert result.templates[0].table_present is False
    assert result.ok is False
