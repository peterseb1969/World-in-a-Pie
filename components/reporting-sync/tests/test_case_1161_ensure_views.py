"""Entity views over version tables, pinned before
ensure_views_for_template's refactor.

Provenance: CASE-1161. The suite never reached these branches: no version
tables (nothing built); no shared columns (nothing built); a cross-version
config selecting no version (the bare view is the identity core); a mapped
column found in no selected table (left out); a selected table with neither
the target nor the source column (a typed NULL); and a bare name held by a
relation that is neither a table nor a view (left alone).
"""

from unittest.mock import AsyncMock

from reporting_sync.models import ReportingConfig

from .test_case_710 import NS, _f, _PgBase


async def _view_def(pg_pool, name: str) -> str | None:
    async with pg_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT pg_get_viewdef(format('%I.%I', $1::text, $2::text)::regclass, true)", NS, name,
        )


async def _view_columns(pg_pool, name: str) -> list[str]:
    async with pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position", NS, name,
        )
    return [r["column_name"] for r in rows]


class TestEnsureViewsBed(_PgBase):
    async def test_no_version_tables_builds_nothing(self, sm, pg_pool):
        assert await sm.ensure_views_for_template(NS, "ghost") is None
        assert await sm.relation_kind(sm.schema_for(NS), "doc_ghost__entities") is None

    async def test_no_shared_columns_builds_nothing(self, sm, pg_pool):
        await self._create_version(sm, "lone", 1, [_f("id")])
        sm._shared_columns = AsyncMock(return_value=[])
        assert await sm.ensure_views_for_template(NS, "lone") is None
        assert await sm.relation_kind(sm.schema_for(NS), "doc_lone__entities") is None

    async def test_mapping_skips_unknown_columns_and_nulls_missing_ones(self, sm, pg_pool):
        await self._create_version(sm, "visit", 1, [_f("id"), _f("smoking")])
        await self._create_version(sm, "visit", 2, [_f("id"), _f("packs")])
        await self._create_version(sm, "visit", 3, [_f("id"), _f("other")])
        cfg = ReportingConfig(cross_version_view={
            "versions": "all",
            "columns": {"packs": {"from": "smoking"}, "ghost": {"from": "nowhere"}},
        })
        assert await sm.ensure_views_for_template(NS, "visit", cfg) is None
        bare = await _view_columns(pg_pool, "doc_visit")
        assert "packs" in bare and "ghost" not in bare
        assert bare[-1] == "packs"
        definition = await _view_def(pg_pool, "doc_visit")
        assert "smoking AS packs" in definition
        assert "NULL::text AS packs" in definition

    async def test_no_selected_version_leaves_the_identity_core(self, sm, pg_pool):
        await self._create_version(sm, "visit", 1, [_f("id"), _f("smoking")])
        await self._create_version(sm, "visit", 2, [_f("id"), _f("packs")])
        cfg = ReportingConfig(cross_version_view={
            "versions": [9], "columns": {"packs": {"from": "smoking"}},
        })
        assert await sm.ensure_views_for_template(NS, "visit", cfg) is None
        assert await _view_columns(pg_pool, "doc_visit") == await _view_columns(pg_pool, "doc_visit__entities")

    async def test_a_bare_name_held_by_another_relation_kind_is_left_alone(self, sm, pg_pool):
        await self._create_version(sm, "visit", 1, [_f("id")])
        async with pg_pool.acquire() as conn:
            await conn.execute(f'CREATE MATERIALIZED VIEW "{sm.schema_for(NS)}"."doc_visit" AS SELECT 1 AS one')
        assert await sm.ensure_views_for_template(NS, "visit") is None
        schema = sm.schema_for(NS)
        assert await sm.relation_kind(schema, "doc_visit") == "m"
        assert await sm.relation_kind(schema, "doc_visit__entities") == "view"
