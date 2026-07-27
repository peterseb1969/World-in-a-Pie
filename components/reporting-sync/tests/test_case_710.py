"""Per-version reporting tables + entity views (CASE-710).

The split's contract, pinned:

- physical tables are per (template, version): ``doc_<value>__v<N>``,
  each shaped by that version's own fields;
- the bare name ``doc_<value>`` is a plain VIEW (identity core by
  default) — the default query surface — and ``doc_<value>__entities``
  always carries the identity core;
- a legacy pre-split physical table under the bare name is reported,
  never auto-dropped, and blocks the view build;
- under latest_only a document lives in exactly ONE version table —
  version-crossing upserts pair with a sibling delete.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from reporting_sync.main import init_postgres_schema
from reporting_sync.models import (
    CrossVersionView,
    FieldType,
    ReportingConfig,
    TemplateField,
)
from reporting_sync.parity import TemplateParity
from reporting_sync.schema_manager import SchemaManager

from .conftest import requires_postgres

NS = "testns710"


def _f(name: str, ftype: FieldType = FieldType.STRING) -> TemplateField:
    return TemplateField(name=name, type=ftype)


class TestVersionedNaming:
    """Pure naming rules — no database needed."""

    def setup_method(self):
        self.sm = SchemaManager(pool=None)  # type: ignore[arg-type]

    def test_version_table_name(self):
        assert self.sm.get_table_name("Sample", version=3) == "doc_sample__v3"

    def test_bare_name_unversioned(self):
        assert self.sm.get_table_name("Sample") == "doc_sample"

    def test_entities_view_name(self):
        assert self.sm.entities_view_name("Sample") == "doc_sample__entities"

    def test_cosmetic_override_composes_with_version(self):
        cfg = ReportingConfig(table_name="samples")
        assert self.sm.get_table_name("Sample", cfg, 2) == "samples__v2"

    def test_qualified_name_carries_version(self):
        assert (
            self.sm.qualified_name("ns", "Sample", version=4)
            == '"ns"."doc_sample__v4"'
        )


class TestCrossVersionViewConfig:
    def test_defaults(self):
        cv = CrossVersionView()
        assert cv.versions == "all"
        assert cv.columns == {}

    def test_reporting_config_accepts_opt_in(self):
        cfg = ReportingConfig(
            cross_version_view={
                "versions": [2, 3],
                "columns": {"packs_per_day": {"from": "smoking"}, "extra": None},
            }
        )
        assert cfg.cross_version_view is not None
        assert cfg.cross_version_view.versions == [2, 3]
        assert cfg.cross_version_view.columns["packs_per_day"] == {"from": "smoking"}


class TestParityModel:
    def test_legacy_table_fails_structural(self):
        row = TemplateParity(
            template_value="x", table_present=True, legacy_table=True
        )
        assert not row.structural_ok

    def test_versioned_healthy_row_passes(self):
        row = TemplateParity(
            template_value="x", table_present=True,
            version_tables=[1, 2], view_present=True,
        )
        assert row.structural_ok


@requires_postgres
class _PgBase:
    pytestmark = pytest.mark.asyncio

    @pytest_asyncio.fixture(autouse=True)
    async def _init(self, pg_pool):
        await init_postgres_schema(pg_pool)
        # Fresh schema per test class run — the tests build specific
        # version-table layouts and views.
        async with pg_pool.acquire() as conn:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{NS}" CASCADE')

    @pytest_asyncio.fixture
    def sm(self, pg_pool):
        return SchemaManager(pg_pool)

    async def _create_version(self, sm, value, version, fields):
        await sm.create_table(
            NS, value, version, fields, None,
            usage="entity", identity_fields=["id"],
        )
        return sm.get_table_name(value, None, version)


class TestPerVersionTables(_PgBase):
    async def test_two_versions_two_tables(self, sm, pg_pool):
        t1 = await self._create_version(sm, "visit", 1, [_f("id"), _f("smoking")])
        t2 = await self._create_version(sm, "visit", 2, [_f("id"), _f("packs_per_day")])
        assert t1 == "doc_visit__v1"
        assert t2 == "doc_visit__v2"
        tables = await sm.list_version_tables(NS, "visit")
        assert tables == {1: "doc_visit__v1", 2: "doc_visit__v2"}

    async def test_each_table_has_its_own_versions_fields(self, sm, pg_pool):
        await self._create_version(sm, "visit", 1, [_f("id"), _f("smoking")])
        await self._create_version(sm, "visit", 2, [_f("id"), _f("packs_per_day")])
        async with pg_pool.acquire() as conn:
            v1_cols = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=$1 AND table_name=$2", NS, "doc_visit__v1")}
            v2_cols = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=$1 AND table_name=$2", NS, "doc_visit__v2")}
        assert "smoking" in v1_cols and "smoking" not in v2_cols
        assert "packs_per_day" in v2_cols and "packs_per_day" not in v1_cols


class TestEntityViews(_PgBase):
    async def test_views_built_over_union(self, sm, pg_pool):
        await self._create_version(sm, "visit", 1, [_f("id"), _f("smoking")])
        await self._create_version(sm, "visit", 2, [_f("id"), _f("packs_per_day")])
        warning = await sm.ensure_views_for_template(NS, "visit")
        assert warning is None

        assert await sm.relation_kind(NS, "doc_visit") == "view"
        assert await sm.relation_kind(NS, "doc_visit__entities") == "view"

        # Rows from both version tables surface through the bare-name view.
        async with pg_pool.acquire() as conn:
            await conn.execute(
                f'INSERT INTO "{NS}"."doc_visit__v1" '
                '(document_id, namespace, template_id, template_version, '
                ' version, status, identity_hash, created_at, id, smoking) '
                "VALUES ('d1', $1, 't', 1, 1, 'active', 'h1', NOW(), 'a', 'yes')",
                NS,
            )
            await conn.execute(
                f'INSERT INTO "{NS}"."doc_visit__v2" '
                '(document_id, namespace, template_id, template_version, '
                ' version, status, identity_hash, created_at, id, packs_per_day) '
                "VALUES ('d2', $1, 't', 2, 1, 'active', 'h2', NOW(), 'b', '2')",
                NS,
            )
            rows = await conn.fetch(f'SELECT document_id, template_version FROM "{NS}"."doc_visit" ORDER BY document_id')
        assert [(r["document_id"], r["template_version"]) for r in rows] == [("d1", 1), ("d2", 2)]

        # The identity-core view carries only columns shared (same type)
        # across every version table — smoking/packs_per_day drop out.
        async with pg_pool.acquire() as conn:
            view_cols = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=$1 AND table_name=$2", NS, "doc_visit__entities")}
        assert "document_id" in view_cols and "id" in view_cols
        assert "smoking" not in view_cols and "packs_per_day" not in view_cols

    async def test_cross_version_opt_in_maps_columns(self, sm, pg_pool):
        await self._create_version(sm, "visit", 1, [_f("id"), _f("smoking")])
        await self._create_version(sm, "visit", 2, [_f("id"), _f("packs_per_day")])
        cfg = ReportingConfig(
            cross_version_view={
                "versions": "all",
                "columns": {"packs_per_day": {"from": "smoking"}},
            }
        )
        warning = await sm.ensure_views_for_template(NS, "visit", cfg)
        assert warning is None
        async with pg_pool.acquire() as conn:
            bare_cols = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=$1 AND table_name=$2", NS, "doc_visit")}
        # The mapped target column is in the combined view; unmapped
        # version-specific columns are still absent.
        assert "packs_per_day" in bare_cols
        assert "smoking" not in bare_cols

    async def test_legacy_table_blocks_view_and_warns(self, sm, pg_pool):
        # Simulate the pre-split layout: a physical table under the bare name.
        async with pg_pool.acquire() as conn:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{NS}"')
            await conn.execute(
                f'CREATE TABLE "{NS}"."doc_visit" (document_id TEXT PRIMARY KEY)'
            )
        await self._create_version(sm, "visit", 2, [_f("id")])
        warning = await sm.ensure_views_for_template(NS, "visit")
        assert warning is not None and "pre-split" in warning
        # The legacy table is untouched — reported, never auto-dropped.
        assert await sm.relation_kind(NS, "doc_visit") == "table"


class TestSiblingDelete(_PgBase):
    async def test_version_crossing_upsert_pairs_with_sibling_delete(self, sm, pg_pool):
        await self._create_version(sm, "visit", 1, [_f("id")])
        await self._create_version(sm, "visit", 2, [_f("id")])
        async with pg_pool.acquire() as conn:
            await conn.execute(
                f'INSERT INTO "{NS}"."doc_visit__v1" '
                '(document_id, namespace, template_id, template_version, '
                ' version, status, identity_hash, created_at, id) '
                "VALUES ('d1', $1, 't', 1, 1, 'active', 'h1', NOW(), 'a')",
                NS,
            )
            await conn.execute(
                f'INSERT INTO "{NS}"."doc_visit__v2" '
                '(document_id, namespace, template_id, template_version, '
                ' version, status, identity_hash, created_at, id) '
                "VALUES ('d1', $1, 't', 2, 2, 'active', 'h1', NOW(), 'a')",
                NS,
            )
        removed = await sm.delete_from_sibling_version_tables(
            NS, "visit", None, keep_version=2, document_id="d1"
        )
        assert removed == 1
        async with pg_pool.acquire() as conn:
            v1_count = await conn.fetchval(
                f'SELECT count(*) FROM "{NS}"."doc_visit__v1"'
            )
            v2_count = await conn.fetchval(
                f'SELECT count(*) FROM "{NS}"."doc_visit__v2"'
            )
        assert (v1_count, v2_count) == (0, 1)
