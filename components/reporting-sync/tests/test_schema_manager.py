"""Behavioural tests for the SchemaManager (CASE-628 rewrite).

Previously these were mock-based and pinned exact DDL *strings*; they broke
wholesale under schema-per-namespace and, worse, tested SQL text rather than
behaviour. They now run the real SchemaManager against a real PostgreSQL
(test-postgres) and assert the *result*: which columns and indexes exist in the
namespace's schema, how schema evolution behaves, and the
ensure_table_for_template orchestration. Table/identity NAME safety and the
per-namespace isolation guarantee live in test_case_628_schema_per_namespace.py.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from reporting_sync.main import init_postgres_schema
from reporting_sync.models import (
    FieldType,
    FileFieldConfig,
    ReportingConfig,
    SemanticType,
    SyncStrategy,
    TemplateField,
)
from reporting_sync.schema_manager import SchemaManager

from .conftest import requires_postgres

NS = "testns"


async def _columns(pool, table: str, schema: str = NS) -> dict[str, str]:
    """Map column_name -> data_type for a table in the namespace schema."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            """,
            schema,
            table,
        )
    return {r["column_name"]: r["data_type"] for r in rows}


async def _indexes(pool, table: str, schema: str = NS) -> set[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname = $1 AND tablename = $2",
            schema,
            table,
        )
    return {r["indexname"] for r in rows}


@requires_postgres
class _Base:
    pytestmark = pytest.mark.asyncio

    @pytest_asyncio.fixture(autouse=True)
    async def _init(self, pg_pool):
        await init_postgres_schema(pg_pool)

    @pytest_asyncio.fixture
    def sm(self, pg_pool):
        return SchemaManager(pg_pool)

    async def _create(self, sm, value, fields, *, config=None, usage="entity",
                      identity_fields=("id",)):
        await sm.create_table(
            NS, value, 1, fields, config,
            usage=usage, identity_fields=list(identity_fields),
        )
        return sm.get_table_name(value, config)


# =========================================================================
# Field-type → column mapping
# =========================================================================


class TestFieldTypeColumns(_Base):
    @pytest.mark.parametrize("ftype,expected_pg", [
        (FieldType.STRING, "text"),
        (FieldType.NUMBER, "numeric"),
        (FieldType.INTEGER, "integer"),
        (FieldType.BOOLEAN, "boolean"),
        (FieldType.DATE, "date"),
        (FieldType.DATETIME, "timestamp with time zone"),
        (FieldType.OBJECT, "jsonb"),
        (FieldType.ARRAY, "jsonb"),
        (FieldType.REFERENCE, "text"),
    ])
    async def test_base_type_column(self, sm, pg_pool, ftype, expected_pg):
        tbl = await self._create(sm, f"t_{ftype.value}", [TemplateField(name="f", type=ftype)])
        cols = await _columns(pg_pool, tbl)
        assert cols.get("f") == expected_pg

    async def test_term_field_creates_value_and_term_id(self, sm, pg_pool):
        tbl = await self._create(sm, "t_term", [TemplateField(name="gender", type=FieldType.TERM)])
        cols = await _columns(pg_pool, tbl)
        assert cols.get("gender") == "text"
        assert cols.get("gender_term_id") == "text"

    async def test_single_file_field_creates_three_columns(self, sm, pg_pool):
        f = TemplateField(name="scan", type=FieldType.FILE,
                          file_config=FileFieldConfig(multiple=False))
        tbl = await self._create(sm, "t_file", [f])
        cols = await _columns(pg_pool, tbl)
        assert cols.get("scan_file_id") == "text"
        assert cols.get("scan_filename") == "text"
        assert cols.get("scan_content_type") == "text"

    async def test_multiple_file_field_is_jsonb(self, sm, pg_pool):
        f = TemplateField(name="scans", type=FieldType.FILE,
                          file_config=FileFieldConfig(multiple=True))
        tbl = await self._create(sm, "t_files", [f])
        cols = await _columns(pg_pool, tbl)
        assert cols.get("scans") == "jsonb"

    @pytest.mark.parametrize("stype,col,pg", [
        (SemanticType.EMAIL, "e", "text"),
        (SemanticType.URL, "u", "text"),
        (SemanticType.LATITUDE, "lat", "numeric"),
        (SemanticType.LONGITUDE, "lon", "numeric"),
        (SemanticType.PERCENTAGE, "pct", "numeric"),
    ])
    async def test_simple_semantic_type(self, sm, pg_pool, stype, col, pg):
        f = TemplateField(name=col, type=FieldType.STRING, semantic_type=stype)
        tbl = await self._create(sm, f"t_sem_{stype.value}", [f])
        cols = await _columns(pg_pool, tbl)
        assert cols.get(col) == pg

    async def test_duration_semantic_creates_three_columns(self, sm, pg_pool):
        f = TemplateField(name="dur", type=FieldType.OBJECT, semantic_type=SemanticType.DURATION)
        tbl = await self._create(sm, "t_dur", [f])
        cols = await _columns(pg_pool, tbl)
        assert cols.get("dur") == "jsonb"
        assert cols.get("dur_seconds") == "numeric"
        assert cols.get("dur_unit_term_id") == "text"

    async def test_geo_point_semantic_creates_three_columns(self, sm, pg_pool):
        f = TemplateField(name="loc", type=FieldType.OBJECT, semantic_type=SemanticType.GEO_POINT)
        tbl = await self._create(sm, "t_geo", [f])
        cols = await _columns(pg_pool, tbl)
        assert cols.get("loc") == "jsonb"
        assert cols.get("loc_latitude") == "numeric"
        assert cols.get("loc_longitude") == "numeric"


# =========================================================================
# System / metadata / JSON columns
# =========================================================================


class TestStructuralColumns(_Base):
    async def test_system_columns_present(self, sm, pg_pool):
        tbl = await self._create(sm, "t_sys", [TemplateField(name="f", type=FieldType.STRING)])
        cols = await _columns(pg_pool, tbl)
        for c in ("document_id", "namespace", "template_id", "template_version",
                  "version", "status", "identity_hash"):
            assert c in cols

    async def test_metadata_columns_default_present(self, sm, pg_pool):
        tbl = await self._create(sm, "t_meta", [TemplateField(name="f", type=FieldType.STRING)])
        cols = await _columns(pg_pool, tbl)
        assert {"created_at", "created_by", "updated_at", "updated_by"} <= set(cols)

    async def test_metadata_columns_excluded_when_disabled(self, sm, pg_pool):
        cfg = ReportingConfig(include_metadata=False)
        tbl = await self._create(sm, "t_nometa", [TemplateField(name="f", type=FieldType.STRING)], config=cfg)
        cols = await _columns(pg_pool, tbl)
        assert "created_by" not in cols

    async def test_json_columns_present(self, sm, pg_pool):
        tbl = await self._create(sm, "t_json", [TemplateField(name="f", type=FieldType.STRING)])
        cols = await _columns(pg_pool, tbl)
        assert {"data_json", "term_references_json", "file_references_json"} <= set(cols)

    async def test_system_column_name_conflict_is_prefixed(self, sm, pg_pool):
        # A data field literally named "status" must not clobber the system column.
        tbl = await self._create(sm, "t_conflict", [TemplateField(name="status", type=FieldType.STRING)])
        cols = await _columns(pg_pool, tbl)
        assert "data_status" in cols


# =========================================================================
# Indexes + sync strategies
# =========================================================================


class TestIndexesAndStrategies(_Base):
    async def _primary_key_cols(self, pg_pool, tbl):
        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = ($1)::regclass AND i.indisprimary
                """,
                f'"{NS}"."{tbl}"',
            )
        return {r["attname"] for r in rows}

    async def test_core_indexes_created(self, sm, pg_pool):
        tbl = await self._create(sm, "t_idx", [TemplateField(name="f", type=FieldType.STRING)])
        idx = await _indexes(pg_pool, tbl)
        assert f"{tbl}_namespace_idx" in idx
        assert f"{tbl}_ns_status_idx" in idx

    async def test_partial_unique_index_when_identity_fields(self, sm, pg_pool):
        tbl = await self._create(sm, "t_uniq", [TemplateField(name="f", type=FieldType.STRING)],
                                 identity_fields=["f"])
        idx = await _indexes(pg_pool, tbl)
        assert f"{tbl}_ns_active_identity_idx" in idx

    async def test_no_partial_unique_index_when_empty_identity_fields(self, sm, pg_pool):
        tbl = await self._create(sm, "t_append", [TemplateField(name="f", type=FieldType.STRING)],
                                 identity_fields=[])
        idx = await _indexes(pg_pool, tbl)
        assert f"{tbl}_ns_active_identity_idx" not in idx

    async def test_latest_only_primary_key_is_document_id(self, sm, pg_pool):
        cfg = ReportingConfig(sync_strategy=SyncStrategy.LATEST_ONLY)
        tbl = await self._create(sm, "t_latest", [TemplateField(name="f", type=FieldType.STRING)], config=cfg)
        assert await self._primary_key_cols(pg_pool, tbl) == {"document_id"}

    async def test_all_versions_primary_key_is_composite(self, sm, pg_pool):
        cfg = ReportingConfig(sync_strategy=SyncStrategy.ALL_VERSIONS)
        tbl = await self._create(sm, "t_allver", [TemplateField(name="f", type=FieldType.STRING)], config=cfg)
        assert await self._primary_key_cols(pg_pool, tbl) == {"document_id", "version"}

    async def test_relationship_template_adds_endpoint_columns_and_indexes(self, sm, pg_pool):
        tbl = await self._create(sm, "t_rel", [TemplateField(name="f", type=FieldType.STRING)],
                                 usage="relationship")
        cols = await _columns(pg_pool, tbl)
        assert "source_ref_id" in cols
        assert "target_ref_id" in cols
        idx = await _indexes(pg_pool, tbl)
        assert f"{tbl}_source_ref_id_idx" in idx
        assert f"{tbl}_target_ref_id_idx" in idx

    async def test_entity_template_has_no_endpoint_columns(self, sm, pg_pool):
        tbl = await self._create(sm, "t_ent", [TemplateField(name="f", type=FieldType.STRING)],
                                 usage="entity")
        cols = await _columns(pg_pool, tbl)
        assert "source_ref_id" not in cols


# =========================================================================
# Schema evolution (update_table_schema)
# =========================================================================


class TestEvolution(_Base):
    async def test_adds_new_column(self, sm, pg_pool):
        await self._create(sm, "t_evo", [TemplateField(name="a", type=FieldType.STRING)], identity_fields=["a"])
        await sm.update_table_schema(
            NS, "t_evo", 2,
            [TemplateField(name="a", type=FieldType.STRING),
             TemplateField(name="b", type=FieldType.INTEGER)],
            None, identity_fields=["a"],
        )
        cols = await _columns(pg_pool, "doc_t_evo")
        assert cols.get("b") == "integer"

    async def test_new_term_field_adds_two_columns(self, sm, pg_pool):
        await self._create(sm, "t_evoterm", [TemplateField(name="a", type=FieldType.STRING)], identity_fields=["a"])
        await sm.update_table_schema(
            NS, "t_evoterm", 2,
            [TemplateField(name="a", type=FieldType.STRING),
             TemplateField(name="g", type=FieldType.TERM)],
            None, identity_fields=["a"],
        )
        cols = await _columns(pg_pool, "doc_t_evoterm")
        assert "g" in cols and "g_term_id" in cols

    async def test_creates_table_if_not_exists(self, sm, pg_pool):
        # update_table_schema on a missing table falls back to create.
        await sm.update_table_schema(
            NS, "t_evonew", 1, [TemplateField(name="a", type=FieldType.STRING)],
            None, identity_fields=["a"],
        )
        assert "a" in await _columns(pg_pool, "doc_t_evonew")

    async def test_drops_legacy_unique_index_for_identity_less_template(self, sm, pg_pool):
        # Create WITH identity (emits the partial-unique index), then evolve to
        # identity-less — the now-broken unique index must be dropped.
        await self._create(sm, "t_drop", [TemplateField(name="a", type=FieldType.STRING)], identity_fields=["a"])
        assert "doc_t_drop_ns_active_identity_idx" in await _indexes(pg_pool, "doc_t_drop")
        await sm.update_table_schema(
            NS, "t_drop", 2, [TemplateField(name="a", type=FieldType.STRING)],
            None, identity_fields=[],
        )
        assert "doc_t_drop_ns_active_identity_idx" not in await _indexes(pg_pool, "doc_t_drop")


# =========================================================================
# ensure_table_for_template orchestration
# =========================================================================


class TestEnsureTableForTemplate(_Base):
    def _template(self, **over):
        t = {
            "value": "person",
            "version": 1,
            "fields": [{"name": "name", "type": "string"}],
            "identity_fields": ["name"],
            "reporting": {"sync_enabled": True},
        }
        t.update(over)
        return t

    async def test_creates_table_when_not_exists(self, sm, pg_pool):
        qualified = await sm.ensure_table_for_template(NS, self._template())
        assert qualified == f'"{NS}"."doc_person"'
        assert "name" in await _columns(pg_pool, "doc_person")

    async def test_updates_schema_when_table_exists(self, sm, pg_pool):
        await sm.ensure_table_for_template(NS, self._template())
        await sm.ensure_table_for_template(NS, self._template(
            version=2,
            fields=[{"name": "name", "type": "string"}, {"name": "age", "type": "integer"}],
        ))
        cols = await _columns(pg_pool, "doc_person")
        assert cols.get("age") == "integer"

    async def test_returns_empty_when_sync_disabled(self, sm, pg_pool):
        out = await sm.ensure_table_for_template(NS, self._template(reporting={"sync_enabled": False}))
        assert out == ""

    async def test_custom_table_name_from_reporting_config(self, sm, pg_pool):
        await sm.ensure_table_for_template(NS, self._template(
            reporting={"sync_enabled": True, "table_name": "people"},
        ))
        assert "name" in await _columns(pg_pool, "people")

    async def test_parses_file_and_semantic_fields(self, sm, pg_pool):
        await sm.ensure_table_for_template(NS, self._template(
            value="mix",
            fields=[
                {"name": "name", "type": "string"},
                {"name": "scan", "type": "file", "file_config": {"multiple": False}},
                {"name": "email", "type": "string", "semantic_type": "email"},
            ],
        ))
        cols = await _columns(pg_pool, "doc_mix")
        assert "scan_file_id" in cols
        assert cols.get("email") == "text"
