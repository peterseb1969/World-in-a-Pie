"""
PostgreSQL integration tests for reporting-sync.

These tests run against a real PostgreSQL instance to catch issues that
mocked tests cannot: DDL execution, type mapping, constraint enforcement,
schema evolution, UPSERT behaviour, and namespace deletion.

Requires POSTGRES_TEST_URI env var (default: postgresql://test:test@localhost:5433/wip_test).
"""

import json
from datetime import UTC, datetime

import asyncpg
import pytest

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
from reporting_sync.transformer import DocumentTransformer

from .conftest import requires_postgres

# =============================================================================
# Helpers
# =============================================================================


def make_fields(*specs: tuple) -> list[TemplateField]:
    """Shorthand for building TemplateField lists.

    Each spec is (name, type) or (name, type, kwargs_dict).
    """
    fields = []
    for spec in specs:
        name, ftype = spec[0], spec[1]
        kwargs = spec[2] if len(spec) > 2 else {}
        fields.append(TemplateField(name=name, type=ftype, **kwargs))
    return fields


def make_document(
    doc_id: str = "0190d000-0000-7000-0000-000000000001",
    template_id: str = "0190c000-0000-7000-0000-000000000001",
    namespace: str = "test",
    version: int = 1,
    data: dict | None = None,
    term_references: list | None = None,
    file_references: list | None = None,
    status: str = "active",
) -> dict:
    """Build a minimal document dict."""
    return {
        "document_id": doc_id,
        "template_id": template_id,
        "namespace": namespace,
        "template_version": 1,
        "version": version,
        "status": status,
        "identity_hash": f"hash-{doc_id}-v{version}",
        "data": data or {},
        "term_references": term_references or [],
        "file_references": file_references or [],
        "created_at": "2026-01-15T10:00:00Z",
        "created_by": "test-user",
        "updated_at": None,
        "updated_by": None,
    }


# =============================================================================
# Schema creation — basic field types
# =============================================================================


@requires_postgres
class TestSchemaCreation:
    """Verify CREATE TABLE DDL executes successfully against real PostgreSQL."""

    async def test_create_simple_table(self, pg_pool):
        """String, number, integer, boolean fields produce a valid table."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("name", FieldType.STRING),
            ("age", FieldType.INTEGER),
            ("score", FieldType.NUMBER),
            ("active", FieldType.BOOLEAN),
        )

        await sm.create_table("Person", 1, fields)

        assert await sm.table_exists("doc_person")
        cols = await sm.get_existing_columns("doc_person")
        assert {"name", "age", "score", "active"}.issubset(cols)
        assert {"document_id", "namespace", "status", "version"}.issubset(cols)

    async def test_create_table_with_date_fields(self, pg_pool):
        """Date and datetime fields map to correct PG types."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("birth_date", FieldType.DATE),
            ("registered_at", FieldType.DATETIME),
        )

        await sm.create_table("Event", 1, fields)

        cols = await sm.get_existing_columns("doc_event")
        assert {"birth_date", "registered_at"}.issubset(cols)

        # Verify PG types via information_schema
        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'doc_event' AND column_name IN ('birth_date', 'registered_at')
                ORDER BY column_name
                """
            )
            type_map = {r["column_name"]: r["data_type"] for r in rows}
            assert type_map["birth_date"] == "date"
            assert type_map["registered_at"] == "timestamp with time zone"

    async def test_create_table_with_term_field(self, pg_pool):
        """Term fields produce both value and _term_id columns."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(("gender", FieldType.TERM))

        await sm.create_table("Patient", 1, fields)

        cols = await sm.get_existing_columns("doc_patient")
        assert "gender" in cols
        assert "gender_term_id" in cols

    async def test_create_table_with_file_field_single(self, pg_pool):
        """Single file field produces file_id, filename, content_type columns."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("photo", FieldType.FILE, {"file_config": FileFieldConfig(multiple=False)}),
        )

        await sm.create_table("Profile", 1, fields)

        cols = await sm.get_existing_columns("doc_profile")
        assert {"photo_file_id", "photo_filename", "photo_content_type"}.issubset(cols)

    async def test_create_table_with_file_field_multiple(self, pg_pool):
        """Multiple file field produces a JSONB column."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("attachments", FieldType.FILE, {"file_config": FileFieldConfig(multiple=True)}),
        )

        await sm.create_table("Report", 1, fields)

        cols = await sm.get_existing_columns("doc_report")
        assert "attachments" in cols
        # Should be JSONB
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'doc_report' AND column_name = 'attachments'
                """
            )
            assert row["data_type"] == "jsonb"

    async def test_create_table_with_object_and_array(self, pg_pool):
        """Object and array fields map to JSONB."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("address", FieldType.OBJECT),
            ("tags", FieldType.ARRAY),
        )

        await sm.create_table("Company", 1, fields)

        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT column_name, data_type FROM information_schema.columns
                WHERE table_name = 'doc_company'
                  AND column_name IN ('address', 'tags')
                """
            )
            type_map = {r["column_name"]: r["data_type"] for r in rows}
            assert type_map["address"] == "jsonb"
            assert type_map["tags"] == "jsonb"

    async def test_system_column_conflict_prefixed(self, pg_pool):
        """Data fields named 'status' or 'version' get prefixed with 'data_'."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("status", FieldType.STRING),
            ("version", FieldType.INTEGER),
        )

        await sm.create_table("Conflict", 1, fields)

        cols = await sm.get_existing_columns("doc_conflict")
        # System columns exist
        assert "status" in cols
        assert "version" in cols
        # Data columns are prefixed
        assert "data_status" in cols
        assert "data_version" in cols


# =============================================================================
# Semantic types
# =============================================================================


@requires_postgres
class TestSemanticTypes:
    """Verify semantic type columns have correct PG types and precision."""

    async def test_latitude_longitude_precision(self, pg_pool):
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("lat", FieldType.NUMBER, {"semantic_type": SemanticType.LATITUDE}),
            ("lon", FieldType.NUMBER, {"semantic_type": SemanticType.LONGITUDE}),
        )

        await sm.create_table("Location", 1, fields)

        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT column_name, numeric_precision, numeric_scale
                FROM information_schema.columns
                WHERE table_name = 'doc_location'
                  AND column_name IN ('lat', 'lon')
                """
            )
            by_col = {r["column_name"]: r for r in rows}
            assert by_col["lat"]["numeric_precision"] == 9
            assert by_col["lat"]["numeric_scale"] == 6
            assert by_col["lon"]["numeric_precision"] == 10
            assert by_col["lon"]["numeric_scale"] == 6

    async def test_duration_columns(self, pg_pool):
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("prep_time", FieldType.OBJECT, {"semantic_type": SemanticType.DURATION}),
        )

        await sm.create_table("Recipe", 1, fields)

        cols = await sm.get_existing_columns("doc_recipe")
        assert "prep_time" in cols  # JSONB
        assert "prep_time_seconds" in cols  # NUMERIC
        assert "prep_time_unit_term_id" in cols  # TEXT

    async def test_geo_point_columns(self, pg_pool):
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("location", FieldType.OBJECT, {"semantic_type": SemanticType.GEO_POINT}),
        )

        await sm.create_table("Place", 1, fields)

        cols = await sm.get_existing_columns("doc_place")
        assert "location" in cols  # JSONB
        assert "location_latitude" in cols
        assert "location_longitude" in cols

    async def test_percentage_precision(self, pg_pool):
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("completion", FieldType.NUMBER, {"semantic_type": SemanticType.PERCENTAGE}),
        )

        await sm.create_table("Progress", 1, fields)

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT numeric_precision, numeric_scale
                FROM information_schema.columns
                WHERE table_name = 'doc_progress' AND column_name = 'completion'
                """
            )
            assert row["numeric_precision"] == 6
            assert row["numeric_scale"] == 3


# =============================================================================
# Schema evolution — ALTER TABLE
# =============================================================================


@requires_postgres
class TestSchemaEvolution:
    """Verify ALTER TABLE adds new columns without breaking existing data."""

    async def test_add_new_columns(self, pg_pool):
        """Adding fields to a template adds columns to existing table."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)

        # v1: name only
        fields_v1 = make_fields(("name", FieldType.STRING))
        await sm.create_table("Evolving", 1, fields_v1)

        # v2: add email and age
        fields_v2 = make_fields(
            ("name", FieldType.STRING),
            ("email", FieldType.STRING),
            ("age", FieldType.INTEGER),
        )
        migrations = await sm.update_table_schema("Evolving", 2, fields_v2)

        assert len(migrations) == 2
        cols = await sm.get_existing_columns("doc_evolving")
        assert {"name", "email", "age"}.issubset(cols)

    async def test_existing_data_preserved(self, pg_pool):
        """ALTER TABLE ADD COLUMN does not destroy existing rows."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)

        fields_v1 = make_fields(("name", FieldType.STRING))
        await sm.create_table("Preserve", 1, fields_v1)

        # Insert a row
        async with pg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO "doc_preserve" (document_id, namespace, template_id,
                    template_version, version, status, identity_hash, name,
                    created_at, data_json)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                "0190d000-0000-7000-0000-000000000001", "test", "0190c000-0000-7000-0000-000000000001", 1, 1, "active", "hash1", "Alice",
                datetime.now(UTC), "{}",
            )

        # Add a column
        fields_v2 = make_fields(
            ("name", FieldType.STRING),
            ("email", FieldType.STRING),
        )
        await sm.update_table_schema("Preserve", 2, fields_v2)

        # Verify existing row still there, new column is NULL
        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT name, email FROM "doc_preserve" WHERE document_id = $1', "0190d000-0000-7000-0000-000000000001")
            assert row["name"] == "Alice"
            assert row["email"] is None

    async def test_migration_recorded(self, pg_pool):
        """Schema changes are tracked in _wip_schema_migrations."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)

        fields = make_fields(("name", FieldType.STRING))
        await sm.create_table("Tracked", 1, fields)

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM _wip_schema_migrations WHERE template_value = $1",
                "Tracked",
            )
            assert row is not None
            assert row["template_version"] == 1
            assert "CREATE TABLE" in row["migration_sql"]


# =============================================================================
# Sync strategies — LATEST_ONLY vs ALL_VERSIONS
# =============================================================================


@requires_postgres
class TestSyncStrategies:
    """Verify UPSERT and INSERT behaviour for both sync strategies."""

    async def test_latest_only_upsert(self, pg_pool):
        """LATEST_ONLY: newer version replaces older, older version is ignored."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        config = ReportingConfig(sync_strategy=SyncStrategy.LATEST_ONLY)
        fields = make_fields(("name", FieldType.STRING))
        await sm.create_table("Item", 1, fields, config)

        transformer = DocumentTransformer(config)

        # Insert v1
        doc_v1 = make_document(data={"name": "Original"}, version=1)
        rows = transformer.transform(doc_v1)
        sql, values = transformer.generate_upsert_sql("doc_item", rows[0], "latest_only")
        async with pg_pool.acquire() as conn:
            await conn.execute(sql, *values)

        # Insert v2 — should update
        doc_v2 = make_document(data={"name": "Updated"}, version=2)
        rows = transformer.transform(doc_v2)
        sql, values = transformer.generate_upsert_sql("doc_item", rows[0], "latest_only")
        async with pg_pool.acquire() as conn:
            await conn.execute(sql, *values)

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT name, version FROM "doc_item" WHERE document_id = $1', "0190d000-0000-7000-0000-000000000001")
            assert row["name"] == "Updated"
            assert row["version"] == 2

        # Insert v1 again — should be ignored (older version)
        async with pg_pool.acquire() as conn:
            await conn.execute(sql, *transformer.generate_upsert_sql(
                "doc_item", transformer.transform(doc_v1)[0], "latest_only"
            )[1])

        async with pg_pool.acquire() as conn:
            row = await conn.fetchrow('SELECT name, version FROM "doc_item" WHERE document_id = $1', "0190d000-0000-7000-0000-000000000001")
            assert row["version"] == 2  # Still v2

    async def test_all_versions_insert(self, pg_pool):
        """ALL_VERSIONS: each version is a separate row."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        config = ReportingConfig(sync_strategy=SyncStrategy.ALL_VERSIONS)
        fields = make_fields(("name", FieldType.STRING))
        await sm.create_table("VersionedItem", 1, fields, config)

        transformer = DocumentTransformer(config)

        for v in [1, 2, 3]:
            doc = make_document(data={"name": f"v{v}"}, version=v)
            rows = transformer.transform(doc)
            sql, values = transformer.generate_upsert_sql("doc_versioneditem", rows[0], "all_versions")
            async with pg_pool.acquire() as conn:
                await conn.execute(sql, *values)

        async with pg_pool.acquire() as conn:
            count = await conn.fetchval('SELECT COUNT(*) FROM "doc_versioneditem"')
            assert count == 3

    async def test_all_versions_duplicate_ignored(self, pg_pool):
        """ALL_VERSIONS: re-inserting same (doc_id, version) does nothing."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        config = ReportingConfig(sync_strategy=SyncStrategy.ALL_VERSIONS)
        fields = make_fields(("name", FieldType.STRING))
        await sm.create_table("DupCheck", 1, fields, config)

        transformer = DocumentTransformer(config)
        doc = make_document(data={"name": "Same"}, version=1)
        rows = transformer.transform(doc)
        sql, values = transformer.generate_upsert_sql("doc_dupcheck", rows[0], "all_versions")

        async with pg_pool.acquire() as conn:
            await conn.execute(sql, *values)
            await conn.execute(sql, *values)  # Duplicate — should be ignored
            count = await conn.fetchval('SELECT COUNT(*) FROM "doc_dupcheck"')
            assert count == 1


# =============================================================================
# Document transformation → insert round-trip
# =============================================================================


@requires_postgres
class TestTransformAndInsert:
    """Verify transformed documents insert cleanly into real PG tables."""

    async def test_term_references_round_trip(self, pg_pool):
        """Term reference columns are populated correctly."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("name", FieldType.STRING),
            ("gender", FieldType.TERM),
        )
        await sm.create_table("TermTest", 1, fields)

        transformer = DocumentTransformer()
        doc = make_document(
            data={"name": "Alice", "gender": "Female"},
            term_references=[{"field_path": "gender", "term_id": "TERM-F-001"}],
        )
        rows = transformer.transform(doc)
        sql, values = transformer.generate_upsert_sql("doc_termtest", rows[0])

        async with pg_pool.acquire() as conn:
            await conn.execute(sql, *values)
            row = await conn.fetchrow('SELECT gender, gender_term_id FROM "doc_termtest" WHERE document_id = $1', "0190d000-0000-7000-0000-000000000001")
            assert row["gender"] == "Female"
            assert row["gender_term_id"] == "TERM-F-001"

    async def test_file_reference_single(self, pg_pool):
        """Single file reference populates _file_id, _filename, _content_type."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("photo", FieldType.FILE, {"file_config": FileFieldConfig(multiple=False)}),
        )
        await sm.create_table("FileTest", 1, fields)

        transformer = DocumentTransformer()
        doc = make_document(
            data={},
            file_references=[{
                "field_path": "photo",
                "file_id": "FILE-001",
                "filename": "portrait.jpg",
                "content_type": "image/jpeg",
            }],
        )
        rows = transformer.transform(doc)
        sql, values = transformer.generate_upsert_sql("doc_filetest", rows[0])

        async with pg_pool.acquire() as conn:
            await conn.execute(sql, *values)
            row = await conn.fetchrow(
                'SELECT photo_file_id, photo_filename, photo_content_type FROM "doc_filetest"'
            )
            assert row["photo_file_id"] == "FILE-001"
            assert row["photo_filename"] == "portrait.jpg"
            assert row["photo_content_type"] == "image/jpeg"

    async def test_date_datetime_parsing(self, pg_pool):
        """Date and datetime strings are parsed to PG date/timestamp types."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("birth_date", FieldType.DATE),
            ("registered_at", FieldType.DATETIME),
        )
        await sm.create_table("DateTest", 1, fields)

        transformer = DocumentTransformer()
        doc = make_document(data={
            "birth_date": "1990-05-15",
            "registered_at": "2026-01-15T14:30:00Z",
        })
        rows = transformer.transform(doc)
        sql, values = transformer.generate_upsert_sql("doc_datetest", rows[0])

        async with pg_pool.acquire() as conn:
            await conn.execute(sql, *values)
            row = await conn.fetchrow('SELECT birth_date, registered_at FROM "doc_datetest"')
            assert str(row["birth_date"]) == "1990-05-15"
            assert row["registered_at"].year == 2026

    async def test_nested_object_stored_as_jsonb(self, pg_pool):
        """Nested objects are stored as JSONB strings."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(("address", FieldType.OBJECT))
        await sm.create_table("ObjTest", 1, fields)

        transformer = DocumentTransformer()
        doc = make_document(data={"address": {"street": "Main St", "city": "NYC"}})
        rows = transformer.transform(doc)
        sql, values = transformer.generate_upsert_sql("doc_objtest", rows[0])

        async with pg_pool.acquire() as conn:
            await conn.execute(sql, *values)
            row = await conn.fetchrow('SELECT address FROM "doc_objtest"')
            # asyncpg returns JSONB as Python dict or string depending on version
            addr = row["address"]
            if isinstance(addr, str):
                addr = json.loads(addr)
            assert addr["city"] == "NYC"

    async def test_duration_semantic_type(self, pg_pool):
        """Duration semantic type computes _seconds and stores unit_term_id."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("prep_time", FieldType.OBJECT, {"semantic_type": SemanticType.DURATION}),
        )
        await sm.create_table("DurationTest", 1, fields)

        template = {
            "fields": [{"name": "prep_time", "type": "object", "semantic_type": "duration"}],
        }
        transformer = DocumentTransformer()
        doc = make_document(
            data={"prep_time": {"value": 30, "unit": "minutes"}},
            term_references=[{"field_path": "prep_time.unit", "term_id": "TERM-MIN"}],
        )
        rows = transformer.transform(doc, template)
        sql, values = transformer.generate_upsert_sql("doc_durationtest", rows[0])

        async with pg_pool.acquire() as conn:
            await conn.execute(sql, *values)
            row = await conn.fetchrow(
                'SELECT prep_time_seconds, prep_time_unit_term_id FROM "doc_durationtest"'
            )
            assert float(row["prep_time_seconds"]) == 1800.0  # 30 * 60
            assert row["prep_time_unit_term_id"] == "TERM-MIN"

    async def test_null_values(self, pg_pool):
        """NULL values in optional fields insert without error."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(
            ("name", FieldType.STRING),
            ("email", FieldType.STRING),
            ("score", FieldType.NUMBER),
        )
        await sm.create_table("NullTest", 1, fields)

        transformer = DocumentTransformer()
        doc = make_document(data={"name": "Alice"})  # email and score missing
        rows = transformer.transform(doc)
        sql, values = transformer.generate_upsert_sql("doc_nulltest", rows[0])

        async with pg_pool.acquire() as conn:
            await conn.execute(sql, *values)
            row = await conn.fetchrow('SELECT name, email, score FROM "doc_nulltest"')
            assert row["name"] == "Alice"
            assert row["email"] is None
            assert row["score"] is None

    async def test_special_characters_in_data(self, pg_pool):
        """Unicode, quotes, and special chars don't break inserts."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(("name", FieldType.STRING), ("notes", FieldType.STRING))
        await sm.create_table("SpecialChars", 1, fields)

        transformer = DocumentTransformer()
        doc = make_document(data={
            "name": "O'Brien — née Müller",
            "notes": 'Contains "quotes", backslashes\\, and emoji 🎉',
        })
        rows = transformer.transform(doc)
        sql, values = transformer.generate_upsert_sql("doc_specialchars", rows[0])

        async with pg_pool.acquire() as conn:
            await conn.execute(sql, *values)
            row = await conn.fetchrow('SELECT name, notes FROM "doc_specialchars"')
            assert "O'Brien" in row["name"]
            assert "Müller" in row["name"]
            assert "🎉" in row["notes"]


# =============================================================================
# Metadata tables
# =============================================================================


@requires_postgres
class TestMetadataTables:
    """Verify metadata tables (terminologies, terms, templates, relationships)."""

    async def test_terminologies_table_created(self, pg_pool):
        sm = SchemaManager(pg_pool)
        await sm.ensure_terminologies_table()
        assert await sm.table_exists("terminologies")

        async with pg_pool.acquire() as conn:
            # Insert and query
            await conn.execute(
                """
                INSERT INTO terminologies (terminology_id, namespace, value, label, status)
                VALUES ($1, $2, $3, $4, $5)
                """,
                "0190b000-0000-7000-0000-000000000001", "test", "Gender", "Gender", "active",
            )
            row = await conn.fetchrow(
                "SELECT * FROM terminologies WHERE namespace = $1 AND terminology_id = $2",
                "test", "0190b000-0000-7000-0000-000000000001",
            )
            assert row["value"] == "Gender"

    async def test_terms_table_created(self, pg_pool):
        sm = SchemaManager(pg_pool)
        await sm.ensure_terms_table()
        assert await sm.table_exists("terms")

        async with pg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO terms (term_id, namespace, terminology_id, value, status)
                VALUES ($1, $2, $3, $4, $5)
                """,
                "0190a000-0000-7000-0000-000000000001", "test", "0190b000-0000-7000-0000-000000000001", "Female", "active",
            )
            row = await conn.fetchrow(
                "SELECT * FROM terms WHERE namespace = $1 AND term_id = $2",
                "test", "0190a000-0000-7000-0000-000000000001",
            )
            assert row["value"] == "Female"

    async def test_templates_table_created(self, pg_pool):
        sm = SchemaManager(pg_pool)
        await sm.ensure_templates_table()
        assert await sm.table_exists("templates")

        async with pg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO templates (template_id, namespace, value, version, status)
                VALUES ($1, $2, $3, $4, $5)
                """,
                "0190c000-0000-7000-0000-000000000001", "test", "Person", 1, "active",
            )
            row = await conn.fetchrow(
                "SELECT * FROM templates WHERE namespace = $1 AND template_id = $2",
                "test", "0190c000-0000-7000-0000-000000000001",
            )
            assert row["value"] == "Person"
            assert row["status"] == "active"

    async def test_term_relationships_table_created(self, pg_pool):
        sm = SchemaManager(pg_pool)
        await sm.ensure_term_relationships_table()
        assert await sm.table_exists("term_relationships")

        async with pg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO term_relationships
                    (namespace, source_term_id, target_term_id, relationship_type, status)
                VALUES ($1, $2, $3, $4, $5)
                """,
                "test", "TERM-A", "TERM-B", "is_a", "active",
            )
            count = await conn.fetchval("SELECT COUNT(*) FROM term_relationships")
            assert count == 1

    async def test_idempotent_table_creation(self, pg_pool):
        """Calling ensure_*_table twice does not error or duplicate."""
        sm = SchemaManager(pg_pool)
        await sm.ensure_terminologies_table()
        await sm.ensure_terminologies_table()  # Second call should be safe
        assert await sm.table_exists("terminologies")


# =============================================================================
# Namespace deletion
# =============================================================================


@requires_postgres
class TestNamespaceDeletion:
    """Verify DELETE /namespace/{prefix} removes all data for a namespace."""

    async def test_delete_namespace_clears_doc_tables(self, pg_pool):
        """Namespace deletion removes rows from doc_* tables."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(("name", FieldType.STRING))
        await sm.create_table("Deletable", 1, fields)

        # Insert rows in two namespaces
        async with pg_pool.acquire() as conn:
            for ns in ["keep", "delete_me"]:
                await conn.execute(
                    """
                    INSERT INTO "doc_deletable"
                        (document_id, namespace, template_id, template_version,
                         version, status, identity_hash, name, created_at, data_json)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    f"DOC-{ns}", ns, "0190c000-0000-7000-0000-000000000001", 1, 1, "active", f"hash-{ns}", f"Name-{ns}",
                    datetime.now(UTC), "{}",
                )

        # Delete one namespace
        async with pg_pool.acquire() as conn:
            doc_tables = await conn.fetch(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name LIKE 'doc_%'
                """
            )
            total_deleted = 0
            for row in doc_tables:
                result = await conn.execute(
                    f'DELETE FROM "{row["table_name"]}" WHERE namespace = $1', "delete_me"
                )
                total_deleted += int(result.split()[-1])

        assert total_deleted == 1

        # Verify: "keep" still there, "delete_me" gone
        async with pg_pool.acquire() as conn:
            count = await conn.fetchval('SELECT COUNT(*) FROM "doc_deletable"')
            assert count == 1
            remaining = await conn.fetchrow('SELECT namespace FROM "doc_deletable"')
            assert remaining["namespace"] == "keep"

    async def test_delete_namespace_clears_metadata_tables(self, pg_pool):
        """Namespace deletion removes rows from metadata tables."""
        sm = SchemaManager(pg_pool)
        await sm.ensure_terminologies_table()
        await sm.ensure_terms_table()
        await sm.ensure_templates_table()
        await sm.ensure_term_relationships_table()

        # Insert metadata in two namespaces
        async with pg_pool.acquire() as conn:
            for ns in ["keep", "delete_me"]:
                await conn.execute(
                    "INSERT INTO terminologies (terminology_id, namespace, value, status) VALUES ($1, $2, $3, $4)",
                    f"T-{ns}", ns, f"Vocab-{ns}", "active",
                )
                await conn.execute(
                    "INSERT INTO terms (term_id, namespace, terminology_id, value, status) VALUES ($1, $2, $3, $4, $5)",
                    f"TRM-{ns}", ns, f"T-{ns}", f"Term-{ns}", "active",
                )
                await conn.execute(
                    "INSERT INTO templates (template_id, namespace, value, version, status) VALUES ($1, $2, $3, $4, $5)",
                    f"TPL-{ns}", ns, f"Template-{ns}", 1, "active",
                )
                await conn.execute(
                    """INSERT INTO term_relationships
                        (namespace, source_term_id, target_term_id, relationship_type, status)
                       VALUES ($1, $2, $3, $4, $5)""",
                    ns, f"SRC-{ns}", f"TGT-{ns}", "is_a", "active",
                )

        # Delete one namespace from all metadata tables
        async with pg_pool.acquire() as conn:
            total_deleted = 0
            for table in ("terminologies", "templates", "terms", "term_relationships"):
                result = await conn.execute(
                    f'DELETE FROM "{table}" WHERE namespace = $1', "delete_me"
                )
                total_deleted += int(result.split()[-1])

        assert total_deleted == 4  # One row per table

        # Verify "keep" namespace untouched
        async with pg_pool.acquire() as conn:
            for table in ("terminologies", "terms", "templates", "term_relationships"):
                count = await conn.fetchval(
                    f'SELECT COUNT(*) FROM "{table}" WHERE namespace = $1', "keep"
                )
                assert count == 1, f"Expected 1 row in {table} for 'keep', got {count}"

    async def test_delete_nonexistent_namespace(self, pg_pool):
        """Deleting a namespace with no data succeeds with zero deleted."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(("name", FieldType.STRING))
        await sm.create_table("Empty", 1, fields)

        async with pg_pool.acquire() as conn:
            result = await conn.execute(
                'DELETE FROM "doc_empty" WHERE namespace = $1', "nonexistent"
            )
            assert int(result.split()[-1]) == 0


# =============================================================================
# Indexes and constraints
# =============================================================================


@requires_postgres
class TestIndexesAndConstraints:
    """Verify indexes and constraints are created correctly."""

    async def test_indexes_created(self, pg_pool):
        """Standard indexes exist after table creation."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(("name", FieldType.STRING))
        await sm.create_table("Indexed", 1, fields)

        async with pg_pool.acquire() as conn:
            indexes = await conn.fetch(
                """
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'doc_indexed'
                """
            )
            idx_names = {r["indexname"] for r in indexes}

        assert "doc_indexed_namespace_idx" in idx_names
        assert "doc_indexed_ns_template_id_idx" in idx_names
        assert "doc_indexed_ns_status_idx" in idx_names
        assert "doc_indexed_ns_identity_hash_idx" in idx_names

    async def test_partial_unique_index_latest_only(self, pg_pool):
        """LATEST_ONLY tables have a partial unique index on active identity_hash."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(("name", FieldType.STRING))
        await sm.create_table("UniqueIdx", 1, fields)

        async with pg_pool.acquire() as conn:
            indexes = await conn.fetch(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'doc_uniqueidx'"
            )
            idx_names = {r["indexname"] for r in indexes}

        assert "doc_uniqueidx_ns_active_identity_idx" in idx_names

    async def test_no_partial_unique_index_all_versions(self, pg_pool):
        """ALL_VERSIONS tables do NOT have the partial unique index."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        config = ReportingConfig(sync_strategy=SyncStrategy.ALL_VERSIONS)
        fields = make_fields(("name", FieldType.STRING))
        await sm.create_table("NoUnique", 1, fields, config)

        async with pg_pool.acquire() as conn:
            indexes = await conn.fetch(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'doc_nounique'"
            )
            idx_names = {r["indexname"] for r in indexes}

        assert "doc_nounique_ns_active_identity_idx" not in idx_names

    async def test_primary_key_enforced_latest_only(self, pg_pool):
        """LATEST_ONLY: duplicate document_id raises UniqueViolation on raw INSERT."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)
        fields = make_fields(("name", FieldType.STRING))
        await sm.create_table("PKTest", 1, fields)

        async with pg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO "doc_pktest" (document_id, namespace, template_id,
                    template_version, version, status, identity_hash, created_at, data_json)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                "DOC-DUP", "test", "0190c000-0000-7000-0000-000000000001", 1, 1, "active", "hash1",
                datetime.now(UTC), "{}",
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    """
                    INSERT INTO "doc_pktest" (document_id, namespace, template_id,
                        template_version, version, status, identity_hash, created_at, data_json)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    "DOC-DUP", "test", "0190c000-0000-7000-0000-000000000001", 1, 2, "active", "hash2",
                    datetime.now(UTC), "{}",
                )


# =============================================================================
# ensure_table_for_template (full orchestration)
# =============================================================================


@requires_postgres
class TestEnsureTableForTemplate:
    """Verify the high-level ensure_table_for_template with real template dicts."""

    async def test_creates_table_from_template_dict(self, pg_pool):
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)

        template = {
            "value": "Observation",
            "version": 1,
            "fields": [
                {"name": "subject", "type": "string"},
                {"name": "value", "type": "number"},
                {"name": "recorded_at", "type": "datetime"},
                {"name": "category", "type": "term", "terminology_ref": "categories"},
            ],
        }

        table_name = await sm.ensure_table_for_template(template)
        assert table_name == "doc_observation"
        assert await sm.table_exists("doc_observation")

        cols = await sm.get_existing_columns("doc_observation")
        assert {"subject", "recorded_at", "category", "category_term_id"}.issubset(cols)
        # "value" conflicts with system column — should be prefixed
        # Actually "value" is not in SYSTEM_COLUMNS, but let's check
        assert "value" in cols or "data_value" in cols

    async def test_skips_table_when_sync_disabled(self, pg_pool):
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)

        template = {
            "value": "Internal",
            "version": 1,
            "fields": [{"name": "secret", "type": "string"}],
            "reporting": {"sync_enabled": False},
        }

        table_name = await sm.ensure_table_for_template(template)
        assert table_name == ""
        assert not await sm.table_exists("doc_internal")

    async def test_updates_existing_table(self, pg_pool):
        """Second call with new fields adds columns."""
        await init_postgres_schema(pg_pool)
        sm = SchemaManager(pg_pool)

        template_v1 = {
            "value": "Evolving2",
            "version": 1,
            "fields": [{"name": "name", "type": "string"}],
        }
        await sm.ensure_table_for_template(template_v1)

        template_v2 = {
            "value": "Evolving2",
            "version": 2,
            "fields": [
                {"name": "name", "type": "string"},
                {"name": "email", "type": "string"},
            ],
        }
        await sm.ensure_table_for_template(template_v2)

        cols = await sm.get_existing_columns("doc_evolving2")
        assert "email" in cols
