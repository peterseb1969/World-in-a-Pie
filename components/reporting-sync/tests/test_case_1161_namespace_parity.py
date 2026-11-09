"""A namespace's parity check, pinned before check_namespace_parity's
refactor.

Provenance: CASE-1161. The suite never reached these branches: a database
with no bookkeeping table at all; template-store unavailable; a template
with sync disabled; the bookkeeping-row check skipped when the bookkeeping
table predates namespace keying; an all_versions template (counts not
comparable); a count mismatch; and a template whose check fails.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from reporting_sync.models import ReportingConfig
from reporting_sync.parity import check_namespace_parity
from reporting_sync.schema_manager import SchemaManager

from .conftest import requires_postgres
from .test_case_689_parity import _patch_http

pytestmark = [pytest.mark.asyncio, requires_postgres]


def _template(value: str, **reporting) -> dict:
    return {"id": f"id-{value}", "value": value, "usage": "entity", "version": 1,
            "fields": [{"name": "title", "type": "string"}], "reporting": reporting}


async def test_no_bookkeeping_table_and_a_disabled_template(pg_pool):
    p_t, p_d = _patch_http([_template("OFF_T", sync_enabled=False)])
    with p_t, p_d:
        result = await check_namespace_parity(pg_pool, "parityns")
    assert result.bookkeeping_tables_ok is True and result.bookkeeping_error is None
    assert result.templates_skipped_sync_disabled == 1 and result.templates == []
    assert result.schema_present is False and result.table_count == 0


async def test_template_store_unavailable(pg_pool):
    with patch("reporting_sync.parity._fetch_namespace_templates",
               new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
        result = await check_namespace_parity(pg_pool, "parityns")
    assert result.ok is False
    assert result.bookkeeping_error == " template-store or document-store unavailable: refused"
    assert result.templates == []


async def test_legacy_bookkeeping_all_versions_mismatch_and_a_failing_template(pg_pool):
    async with pg_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE _wip_schema_migrations (
                namespace VARCHAR(255) NOT NULL DEFAULT 'wip', template_value TEXT NOT NULL,
                template_version INTEGER NOT NULL, migration_sql TEXT NOT NULL,
                PRIMARY KEY (namespace, template_value, template_version))
        """)
    sm = SchemaManager(pg_pool)
    fields = SchemaManager.parse_template_fields(_template("X")["fields"])
    await sm.create_table("parityns", "ALL_T", 1, fields, ReportingConfig(sync_strategy="all_versions"))
    await sm.create_table("parityns", "MIS_T", 1, fields, ReportingConfig())
    # Then the bookkeeping table is swapped for one that predates namespace keying.
    async with pg_pool.acquire() as conn:
        await conn.execute("DROP TABLE _wip_schema_migrations")
        await conn.execute("""
            CREATE TABLE _wip_schema_migrations (
                template_value TEXT NOT NULL, template_version INTEGER NOT NULL,
                migration_sql TEXT NOT NULL, PRIMARY KEY (template_value, template_version))
        """)
    broken = _template("BAD_T")
    broken["version"] = "not-a-number"

    p_t, p_d = _patch_http(
        [_template("ALL_T", sync_strategy="all_versions"), _template("MIS_T"), broken], doc_total=2,
    )
    with p_t, p_d:
        result = await check_namespace_parity(pg_pool, "parityns", include_counts=True)

    rows = {r.template_value: r for r in result.templates}
    assert result.bookkeeping_tables_ok is False
    assert rows["ALL_T"].counts_comparable is False
    assert rows["ALL_T"].bookkeeping_row is False  # not checked: the bookkeeping table is unusable
    assert (rows["MIS_T"].expected_documents, rows["MIS_T"].actual_rows, rows["MIS_T"].counts_match) == (2, 0, False)
    assert rows["BAD_T"].error and "not-a-number" in rows["BAD_T"].error
    assert result.count_mismatches == 1
    assert result.structural_issues == sum(1 for r in result.templates if not r.structural_ok)
    assert result.ok is False
