"""CASE-628 — reporting tables live in per-namespace PostgreSQL schemas.

The core guarantee: two namespaces that define the same template value get two
distinct tables ("a"."doc_x" vs "b"."doc_x") instead of silently sharing one
public.doc_x and merging schemas. These exercise the real SchemaManager against
a real PostgreSQL (test-postgres), plus the pure-function identity safety.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from reporting_sync.main import init_postgres_schema
from reporting_sync.models import FieldType, ReportingConfig, TemplateField
from reporting_sync.schema_manager import SchemaManager

from .conftest import requires_postgres


def _fields() -> list[TemplateField]:
    return [
        TemplateField(name="name", type=FieldType.STRING),
        TemplateField(name="age", type=FieldType.INTEGER),
    ]


async def _table_in_schema(pool, schema: str, table: str) -> bool:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_schema = $1 AND table_name = $2
            )
            """,
            schema,
            table,
        )


async def _schema_exists(pool, schema: str) -> bool:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT EXISTS (SELECT FROM pg_namespace WHERE nspname = $1)", schema
        )


# ---------------------------------------------------------------------------
# Pure-function identity safety (no Postgres needed)
# ---------------------------------------------------------------------------


class TestSafeIdent:
    def test_qualified_name_is_schema_qualified(self):
        sm = SchemaManager(None)  # no DB needed for name building
        assert sm.qualified_name("clinicA", "patient") == '"clinicA"."doc_patient"'

    def test_get_table_name_is_bare(self):
        sm = SchemaManager(None)
        assert sm.get_table_name("Patient") == "doc_patient"

    def test_config_table_name_override(self):
        sm = SchemaManager(None)
        cfg = ReportingConfig(table_name="custom_tbl")
        assert sm.get_table_name("patient", cfg) == "custom_tbl"

    def test_hyphenated_namespace_quotes_safely(self):
        sm = SchemaManager(None)
        assert sm.qualified_name("customer-abc", "patient") == '"customer-abc"."doc_patient"'

    def test_rejects_embedded_double_quote(self):
        sm = SchemaManager(None)
        with pytest.raises(ValueError):
            sm.schema_for('evil"; DROP SCHEMA public; --')

    def test_rejects_over_63_bytes(self):
        sm = SchemaManager(None)
        with pytest.raises(ValueError):
            sm.schema_for("n" * 64)


# ---------------------------------------------------------------------------
# Real-Postgres schema behaviour
# ---------------------------------------------------------------------------


@requires_postgres
class TestPerNamespaceSchemas:
    pytestmark = pytest.mark.asyncio

    @pytest_asyncio.fixture(autouse=True)
    async def _init_bookkeeping(self, pg_pool):
        """create_table records into _wip_schema_migrations — ensure the
        bookkeeping tables exist (runs after pg_pool's clean)."""
        await init_postgres_schema(pg_pool)

    async def test_ensure_schema_creates_pg_schema(self, pg_pool):
        sm = SchemaManager(pg_pool)
        assert not await _schema_exists(pg_pool, "clinic_a")
        await sm.ensure_schema("clinic_a")
        assert await _schema_exists(pg_pool, "clinic_a")

    async def test_create_table_lands_in_namespace_schema(self, pg_pool):
        sm = SchemaManager(pg_pool)
        await sm.create_table("clinic_a", "patient", 1, _fields(), identity_fields=["name"])
        assert await _table_in_schema(pg_pool, "clinic_a", "doc_patient")
        # And it is NOT in public.
        assert not await _table_in_schema(pg_pool, "public", "doc_patient")

    async def test_same_value_two_namespaces_no_collision(self, pg_pool):
        """The core fix: same template value in two namespaces → two tables."""
        sm = SchemaManager(pg_pool)
        await sm.create_table("clinic_a", "patient", 1, _fields(), identity_fields=["name"])
        await sm.create_table("clinic_b", "patient", 1, _fields(), identity_fields=["name"])

        assert await _table_in_schema(pg_pool, "clinic_a", "doc_patient")
        assert await _table_in_schema(pg_pool, "clinic_b", "doc_patient")

        # They are physically distinct: a row in one is invisible to the other.
        async with pg_pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO "clinic_a"."doc_patient" '
                "(document_id, namespace, template_id, template_version, version, "
                "status, identity_hash, created_at) "
                "VALUES ('d1','clinic_a','t1',1,1,'active','h1', NOW())"
            )
            a_count = await conn.fetchval('SELECT count(*) FROM "clinic_a"."doc_patient"')
            b_count = await conn.fetchval('SELECT count(*) FROM "clinic_b"."doc_patient"')
        assert a_count == 1
        assert b_count == 0

    async def test_hyphenated_namespace_end_to_end(self, pg_pool):
        sm = SchemaManager(pg_pool)
        await sm.create_table("dev-wip-song", "track", 1, _fields(), identity_fields=["name"])
        assert await _table_in_schema(pg_pool, "dev-wip-song", "doc_track")

    async def test_ensure_table_for_template_returns_qualified(self, pg_pool):
        sm = SchemaManager(pg_pool)
        template = {
            "value": "patient",
            "version": 1,
            "fields": [{"name": "name", "type": "string"}],
            "identity_fields": ["name"],
            "reporting": {"sync_enabled": True},
        }
        qualified = await sm.ensure_table_for_template("clinic_a", template)
        assert qualified == '"clinic_a"."doc_patient"'
        assert await _table_in_schema(pg_pool, "clinic_a", "doc_patient")

    async def test_metadata_tables_are_per_namespace(self, pg_pool):
        sm = SchemaManager(pg_pool)
        await sm.ensure_terminologies_table("clinic_a")
        await sm.ensure_terms_table("clinic_b")
        assert await _table_in_schema(pg_pool, "clinic_a", "terminologies")
        assert await _table_in_schema(pg_pool, "clinic_b", "terms")
        # clinic_a did not get a terms table, clinic_b did not get terminologies.
        assert not await _table_in_schema(pg_pool, "clinic_a", "terms")
        assert not await _table_in_schema(pg_pool, "clinic_b", "terminologies")


@requires_postgres
class TestZeroDocumentMaterialization:
    """CASE-636 — a batch sync over a zero-document template creates the empty
    table (real DDL): a freshly bootstrapped namespace must be SQL-queryable,
    not fail with relation-does-not-exist until its first document."""

    @pytest_asyncio.fixture(autouse=True)
    async def _init_bookkeeping(self, pg_pool):
        await init_postgres_schema(pg_pool)

    async def test_batch_sync_zero_docs_creates_empty_table(self, pg_pool):
        from unittest.mock import AsyncMock

        from reporting_sync.batch_sync import BatchSyncJob, BatchSyncService, BatchSyncStatus

        svc = BatchSyncService(pg_pool)
        svc._fetch_template_by_value = AsyncMock(
            return_value={
                "template_id": "TPL-VAL-1",
                "value": "val_template",
                "namespace": "wip-val",
                "version": 1,
                "fields": [{"name": "name", "type": "string"}],
                "identity_fields": ["name"],
                "reporting": {"sync_enabled": True},
            }
        )
        svc._fetch_documents = AsyncMock(return_value=([], 0))

        job = BatchSyncJob(
            job_id="t636", template_value="val_template", status=BatchSyncStatus.PENDING
        )
        await svc._run_batch_sync(job, force=False, page_size=100)

        assert job.status == BatchSyncStatus.COMPLETED
        assert await _table_in_schema(pg_pool, "wip-val", "doc_val_template")
        # And it is actually queryable — empty result, not an error.
        async with pg_pool.acquire() as conn:
            count = await conn.fetchval('SELECT COUNT(*) FROM "wip-val"."doc_val_template"')
        assert count == 0
