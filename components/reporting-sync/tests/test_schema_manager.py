"""
Tests for the SchemaManager.

Covers DDL generation for various field types, ALTER TABLE schema evolution,
table existence checks, and the ensure_table_for_template orchestration.
All async database operations are mocked via asyncpg pool/connection mocks.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from reporting_sync.models import (
    FieldType,
    FileFieldConfig,
    ReportingConfig,
    SemanticType,
    SyncStrategy,
    TemplateField,
)
from reporting_sync.schema_manager import SchemaManager


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def mock_pool():
    """Mock asyncpg pool with async context manager support."""
    pool = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])

    acm = AsyncMock()
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = acm

    return pool, conn


@pytest.fixture
def sm(mock_pool):
    """SchemaManager wired to the mock pool."""
    pool, conn = mock_pool
    return SchemaManager(pool)


# =========================================================================
# DDL Generation - Basic Field Types
# =========================================================================


class TestCreateTableDDL:
    """Tests for generate_create_table_ddl with various field types."""

    def _make_sm(self):
        pool = MagicMock()
        return SchemaManager(pool)

    def test_string_field(self):
        sm = self._make_sm()
        fields = [TemplateField(name="name", type=FieldType.STRING)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"name" TEXT' in ddl

    def test_number_field(self):
        sm = self._make_sm()
        fields = [TemplateField(name="weight", type=FieldType.NUMBER)]
        ddl = sm.generate_create_table_ddl("measurement", 1, fields)
        assert '"weight" NUMERIC' in ddl

    def test_integer_field(self):
        sm = self._make_sm()
        fields = [TemplateField(name="age", type=FieldType.INTEGER)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"age" INTEGER' in ddl

    def test_boolean_field(self):
        sm = self._make_sm()
        fields = [TemplateField(name="active", type=FieldType.BOOLEAN)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"active" BOOLEAN' in ddl

    def test_date_field(self):
        sm = self._make_sm()
        fields = [TemplateField(name="birth_date", type=FieldType.DATE)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"birth_date" DATE' in ddl

    def test_datetime_field(self):
        sm = self._make_sm()
        fields = [TemplateField(name="submitted_at", type=FieldType.DATETIME)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"submitted_at" TIMESTAMP WITH TIME ZONE' in ddl

    def test_object_field(self):
        sm = self._make_sm()
        fields = [TemplateField(name="address", type=FieldType.OBJECT)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"address" JSONB' in ddl

    def test_array_field(self):
        sm = self._make_sm()
        fields = [TemplateField(name="tags", type=FieldType.ARRAY)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"tags" JSONB' in ddl

    def test_reference_field(self):
        sm = self._make_sm()
        fields = [TemplateField(name="parent_id", type=FieldType.REFERENCE)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"parent_id" TEXT' in ddl


# =========================================================================
# DDL Generation - Term Fields
# =========================================================================


class TestCreateTableDDLTermFields:
    """Tests for term field column generation (value + term_id)."""

    def _make_sm(self):
        pool = MagicMock()
        return SchemaManager(pool)

    def test_term_field_creates_two_columns(self):
        sm = self._make_sm()
        fields = [TemplateField(name="country", type=FieldType.TERM)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"country" TEXT' in ddl
        assert '"country_term_id" TEXT' in ddl

    def test_multiple_term_fields(self):
        sm = self._make_sm()
        fields = [
            TemplateField(name="country", type=FieldType.TERM),
            TemplateField(name="gender", type=FieldType.TERM),
        ]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"country" TEXT' in ddl
        assert '"country_term_id" TEXT' in ddl
        assert '"gender" TEXT' in ddl
        assert '"gender_term_id" TEXT' in ddl


# =========================================================================
# DDL Generation - File Fields
# =========================================================================


class TestCreateTableDDLFileFields:
    """Tests for file field column generation."""

    def _make_sm(self):
        pool = MagicMock()
        return SchemaManager(pool)

    def test_single_file_field_creates_three_columns(self):
        sm = self._make_sm()
        fields = [TemplateField(name="photo", type=FieldType.FILE)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"photo_file_id" TEXT' in ddl
        assert '"photo_filename" TEXT' in ddl
        assert '"photo_content_type" TEXT' in ddl

    def test_multiple_file_field_creates_jsonb(self):
        sm = self._make_sm()
        fields = [
            TemplateField(
                name="attachments",
                type=FieldType.FILE,
                file_config=FileFieldConfig(multiple=True),
            )
        ]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"attachments" JSONB' in ddl
        # Should NOT have individual columns for multiple files
        assert '"attachments_file_id"' not in ddl


# =========================================================================
# DDL Generation - Semantic Types
# =========================================================================


class TestCreateTableDDLSemanticTypes:
    """Tests for semantic type column generation."""

    def _make_sm(self):
        pool = MagicMock()
        return SchemaManager(pool)

    def test_email_semantic_type(self):
        sm = self._make_sm()
        fields = [
            TemplateField(name="email", type=FieldType.STRING, semantic_type=SemanticType.EMAIL)
        ]
        ddl = sm.generate_create_table_ddl("contact", 1, fields)
        assert '"email" TEXT' in ddl

    def test_url_semantic_type(self):
        sm = self._make_sm()
        fields = [
            TemplateField(name="website", type=FieldType.STRING, semantic_type=SemanticType.URL)
        ]
        ddl = sm.generate_create_table_ddl("contact", 1, fields)
        assert '"website" TEXT' in ddl

    def test_latitude_semantic_type(self):
        sm = self._make_sm()
        fields = [
            TemplateField(name="lat", type=FieldType.NUMBER, semantic_type=SemanticType.LATITUDE)
        ]
        ddl = sm.generate_create_table_ddl("location", 1, fields)
        assert '"lat" NUMERIC(9,6)' in ddl

    def test_longitude_semantic_type(self):
        sm = self._make_sm()
        fields = [
            TemplateField(name="lon", type=FieldType.NUMBER, semantic_type=SemanticType.LONGITUDE)
        ]
        ddl = sm.generate_create_table_ddl("location", 1, fields)
        assert '"lon" NUMERIC(10,6)' in ddl

    def test_percentage_semantic_type(self):
        sm = self._make_sm()
        fields = [
            TemplateField(name="score", type=FieldType.NUMBER, semantic_type=SemanticType.PERCENTAGE)
        ]
        ddl = sm.generate_create_table_ddl("result", 1, fields)
        assert '"score" NUMERIC(6,3)' in ddl

    def test_duration_semantic_type_creates_three_columns(self):
        sm = self._make_sm()
        fields = [
            TemplateField(name="wait_time", type=FieldType.OBJECT, semantic_type=SemanticType.DURATION)
        ]
        ddl = sm.generate_create_table_ddl("event", 1, fields)
        assert '"wait_time" JSONB' in ddl
        assert '"wait_time_seconds" NUMERIC' in ddl
        assert '"wait_time_unit_term_id" TEXT' in ddl

    def test_geo_point_semantic_type_creates_three_columns(self):
        sm = self._make_sm()
        fields = [
            TemplateField(name="location", type=FieldType.OBJECT, semantic_type=SemanticType.GEO_POINT)
        ]
        ddl = sm.generate_create_table_ddl("place", 1, fields)
        assert '"location" JSONB' in ddl
        assert '"location_latitude" NUMERIC(9,6)' in ddl
        assert '"location_longitude" NUMERIC(10,6)' in ddl


# =========================================================================
# DDL Generation - System Columns & Indexes
# =========================================================================


class TestCreateTableDDLSystemColumns:
    """Tests for system columns, metadata, and indexes."""

    def _make_sm(self):
        pool = MagicMock()
        return SchemaManager(pool)

    def test_system_columns_always_present(self):
        sm = self._make_sm()
        fields = [TemplateField(name="name", type=FieldType.STRING)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        for col in ["document_id", "namespace", "template_id", "template_version",
                     "version", "status", "identity_hash"]:
            assert f'"{col}"' in ddl

    def test_metadata_columns_present_by_default(self):
        sm = self._make_sm()
        fields = [TemplateField(name="name", type=FieldType.STRING)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"created_at"' in ddl
        assert '"created_by"' in ddl
        assert '"updated_at"' in ddl
        assert '"updated_by"' in ddl

    def test_metadata_columns_excluded_when_disabled(self):
        sm = self._make_sm()
        config = ReportingConfig(include_metadata=False)
        fields = [TemplateField(name="name", type=FieldType.STRING)]
        ddl = sm.generate_create_table_ddl("person", 1, fields, config)
        assert '"created_at"' not in ddl
        assert '"updated_at"' not in ddl

    def test_json_columns_always_present(self):
        sm = self._make_sm()
        fields = [TemplateField(name="name", type=FieldType.STRING)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"data_json" JSONB' in ddl
        assert '"term_references_json" JSONB' in ddl
        assert '"file_references_json" JSONB' in ddl

    def test_indexes_created(self):
        sm = self._make_sm()
        fields = [TemplateField(name="name", type=FieldType.STRING)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert "_namespace_idx" in ddl
        assert "_ns_template_id_idx" in ddl
        assert "_ns_status_idx" in ddl
        assert "_ns_identity_hash_idx" in ddl
        assert "_ns_created_at_idx" in ddl

    def test_system_column_name_conflict_prefixed(self):
        """Field names conflicting with system columns are prefixed with 'data_'."""
        sm = self._make_sm()
        fields = [TemplateField(name="status", type=FieldType.STRING)]
        ddl = sm.generate_create_table_ddl("person", 1, fields)
        assert '"data_status" TEXT' in ddl

    def test_custom_table_name(self):
        sm = self._make_sm()
        config = ReportingConfig(table_name="custom_table")
        fields = [TemplateField(name="name", type=FieldType.STRING)]
        ddl = sm.generate_create_table_ddl("person", 1, fields, config)
        assert '"custom_table"' in ddl

    def test_default_table_name(self):
        sm = self._make_sm()
        table = sm.get_table_name("Person")
        assert table == "doc_person"


# =========================================================================
# DDL Generation - Sync Strategies
# =========================================================================


class TestCreateTableDDLStrategies:
    """Tests for sync strategy differences in DDL (covered in test_transformer.py
    but included here for completeness of schema_manager tests)."""

    def _make_sm(self):
        pool = MagicMock()
        return SchemaManager(pool)

    def test_latest_only_single_pk(self):
        sm = self._make_sm()
        config = ReportingConfig(sync_strategy=SyncStrategy.LATEST_ONLY)
        fields = [TemplateField(name="name", type=FieldType.STRING)]
        ddl = sm.generate_create_table_ddl("person", 1, fields, config)
        assert 'document_id" TEXT PRIMARY KEY' in ddl
        assert "PRIMARY KEY (document_id, version)" not in ddl

    def test_all_versions_composite_pk(self):
        sm = self._make_sm()
        config = ReportingConfig(sync_strategy=SyncStrategy.ALL_VERSIONS)
        fields = [TemplateField(name="name", type=FieldType.STRING)]
        ddl = sm.generate_create_table_ddl("person", 1, fields, config)
        assert "PRIMARY KEY (document_id, version)" in ddl
        assert 'document_id" TEXT PRIMARY KEY' not in ddl

    def test_latest_only_has_partial_unique_index(self):
        sm = self._make_sm()
        config = ReportingConfig(sync_strategy=SyncStrategy.LATEST_ONLY)
        fields = [TemplateField(name="name", type=FieldType.STRING)]
        ddl = sm.generate_create_table_ddl("person", 1, fields, config)
        assert "_ns_active_identity_idx" in ddl
        assert "WHERE status = 'active'" in ddl

    def test_all_versions_no_partial_unique_index(self):
        sm = self._make_sm()
        config = ReportingConfig(sync_strategy=SyncStrategy.ALL_VERSIONS)
        fields = [TemplateField(name="name", type=FieldType.STRING)]
        ddl = sm.generate_create_table_ddl("person", 1, fields, config)
        assert "_ns_active_identity_idx" not in ddl


# =========================================================================
# Async Operations - table_exists
# =========================================================================


class TestTableExists:
    """Tests for the table_exists async method."""

    @pytest.mark.asyncio
    async def test_table_exists_returns_true(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=True)
        sm = SchemaManager(pool)
        result = await sm.table_exists("doc_person")
        assert result is True
        conn.fetchval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_table_not_exists_returns_false(self, mock_pool):
        pool, conn = mock_pool
        conn.fetchval = AsyncMock(return_value=False)
        sm = SchemaManager(pool)
        result = await sm.table_exists("doc_nonexistent")
        assert result is False


# =========================================================================
# Async Operations - update_table_schema
# =========================================================================


class TestUpdateTableSchema:
    """Tests for ALTER TABLE schema evolution."""

    @pytest.mark.asyncio
    async def test_adds_new_column(self, mock_pool):
        """New field not in existing columns triggers ALTER TABLE ADD COLUMN."""
        pool, conn = mock_pool
        sm = SchemaManager(pool)

        # Simulate table exists with just the name column
        sm.table_exists = AsyncMock(return_value=True)
        sm.get_existing_columns = AsyncMock(return_value={
            "document_id", "namespace", "template_id", "template_version",
            "version", "status", "identity_hash",
            "created_at", "created_by", "updated_at", "updated_by",
            "name", "data_json", "term_references_json", "file_references_json",
        })

        fields = [
            TemplateField(name="name", type=FieldType.STRING),
            TemplateField(name="age", type=FieldType.INTEGER),  # new field
        ]

        migrations = await sm.update_table_schema("person", 2, fields)

        assert len(migrations) == 1
        assert "ADD COLUMN" in migrations[0]
        assert '"age"' in migrations[0]
        assert "INTEGER" in migrations[0]

    @pytest.mark.asyncio
    async def test_no_migration_when_no_new_columns(self, mock_pool):
        """When all fields already exist, no ALTER TABLE is generated."""
        pool, conn = mock_pool
        sm = SchemaManager(pool)

        sm.table_exists = AsyncMock(return_value=True)
        sm.get_existing_columns = AsyncMock(return_value={
            "document_id", "namespace", "template_id", "template_version",
            "version", "status", "identity_hash",
            "created_at", "created_by", "updated_at", "updated_by",
            "name", "data_json", "term_references_json", "file_references_json",
        })

        fields = [TemplateField(name="name", type=FieldType.STRING)]
        migrations = await sm.update_table_schema("person", 2, fields)
        assert migrations == []

    @pytest.mark.asyncio
    async def test_creates_table_if_not_exists(self, mock_pool):
        """If table does not exist, update_table_schema creates it."""
        pool, conn = mock_pool
        sm = SchemaManager(pool)

        sm.table_exists = AsyncMock(return_value=False)
        sm.create_table = AsyncMock(return_value="CREATE TABLE ...")

        fields = [TemplateField(name="name", type=FieldType.STRING)]
        migrations = await sm.update_table_schema("person", 1, fields)

        sm.create_table.assert_awaited_once()
        assert len(migrations) == 1
        assert "Created table" in migrations[0]

    @pytest.mark.asyncio
    async def test_new_term_field_adds_two_columns(self, mock_pool):
        """Adding a new term field adds both value and term_id columns."""
        pool, conn = mock_pool
        sm = SchemaManager(pool)

        sm.table_exists = AsyncMock(return_value=True)
        sm.get_existing_columns = AsyncMock(return_value={
            "document_id", "namespace", "template_id", "template_version",
            "version", "status", "identity_hash",
            "created_at", "created_by", "updated_at", "updated_by",
            "name", "data_json", "term_references_json", "file_references_json",
        })

        fields = [
            TemplateField(name="name", type=FieldType.STRING),
            TemplateField(name="country", type=FieldType.TERM),  # new
        ]

        migrations = await sm.update_table_schema("person", 2, fields)

        assert len(migrations) == 2
        col_names = [m.split('"')[3] for m in migrations if "ADD COLUMN" in m]
        assert "country" in col_names
        assert "country_term_id" in col_names

    @pytest.mark.asyncio
    async def test_alter_table_strips_not_null_and_pk(self, mock_pool):
        """ALTER TABLE ADD COLUMN strips NOT NULL and PRIMARY KEY constraints."""
        pool, conn = mock_pool
        sm = SchemaManager(pool)

        sm.table_exists = AsyncMock(return_value=True)
        sm.get_existing_columns = AsyncMock(return_value={
            "document_id", "namespace", "template_id", "template_version",
            "version", "status", "identity_hash",
            "created_at", "created_by", "updated_at", "updated_by",
            "data_json", "term_references_json", "file_references_json",
        })

        fields = [TemplateField(name="email", type=FieldType.STRING)]
        migrations = await sm.update_table_schema("person", 2, fields)

        assert len(migrations) == 1
        assert "NOT NULL" not in migrations[0]
        assert "PRIMARY KEY" not in migrations[0]

    @pytest.mark.asyncio
    async def test_records_migration_in_schema_migrations_table(self, mock_pool):
        """Migrations are recorded in _wip_schema_migrations."""
        pool, conn = mock_pool
        sm = SchemaManager(pool)

        sm.table_exists = AsyncMock(return_value=True)
        sm.get_existing_columns = AsyncMock(return_value={
            "document_id", "namespace", "template_id", "template_version",
            "version", "status", "identity_hash",
            "created_at", "created_by", "updated_at", "updated_by",
            "data_json", "term_references_json", "file_references_json",
        })

        fields = [TemplateField(name="email", type=FieldType.STRING)]
        await sm.update_table_schema("person", 2, fields)

        # Check that conn.execute was called with INSERT INTO _wip_schema_migrations
        calls = conn.execute.call_args_list
        migration_call = [c for c in calls if "_wip_schema_migrations" in str(c)]
        assert len(migration_call) >= 1


# =========================================================================
# Async Operations - ensure_table_for_template
# =========================================================================


class TestEnsureTableForTemplate:
    """Tests for the ensure_table_for_template orchestration method."""

    @pytest.mark.asyncio
    async def test_creates_table_when_not_exists(self, mock_pool):
        pool, conn = mock_pool
        sm = SchemaManager(pool)
        sm.table_exists = AsyncMock(return_value=False)
        sm.create_table = AsyncMock(return_value="CREATE TABLE ...")

        template = {
            "value": "person",
            "version": 1,
            "fields": [
                {"name": "name", "type": "string"},
                {"name": "email", "type": "string"},
            ],
        }

        table_name = await sm.ensure_table_for_template(template)

        assert table_name == "doc_person"
        sm.create_table.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updates_schema_when_table_exists(self, mock_pool):
        pool, conn = mock_pool
        sm = SchemaManager(pool)
        sm.table_exists = AsyncMock(return_value=True)
        sm.update_table_schema = AsyncMock(return_value=["ALTER TABLE ..."])

        template = {
            "value": "person",
            "version": 2,
            "fields": [
                {"name": "name", "type": "string"},
                {"name": "email", "type": "string"},
                {"name": "phone", "type": "string"},
            ],
        }

        table_name = await sm.ensure_table_for_template(template)

        assert table_name == "doc_person"
        sm.update_table_schema.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_sync_disabled(self, mock_pool):
        pool, conn = mock_pool
        sm = SchemaManager(pool)

        template = {
            "value": "person",
            "version": 1,
            "fields": [{"name": "name", "type": "string"}],
            "reporting": {"sync_enabled": False},
        }

        table_name = await sm.ensure_table_for_template(template)
        assert table_name == ""

    @pytest.mark.asyncio
    async def test_parses_term_fields_correctly(self, mock_pool):
        pool, conn = mock_pool
        sm = SchemaManager(pool)
        sm.table_exists = AsyncMock(return_value=False)
        sm.create_table = AsyncMock(return_value="CREATE TABLE ...")

        template = {
            "value": "person",
            "version": 1,
            "fields": [
                {"name": "country", "type": "term", "terminology_ref": "COUNTRIES"},
            ],
        }

        table_name = await sm.ensure_table_for_template(template)
        assert table_name == "doc_person"

        # Verify the create_table was called with a TemplateField of type TERM
        call_args = sm.create_table.call_args
        fields = call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("fields")
        assert fields[0].type == FieldType.TERM
        assert fields[0].terminology_ref == "COUNTRIES"

    @pytest.mark.asyncio
    async def test_parses_file_fields_correctly(self, mock_pool):
        pool, conn = mock_pool
        sm = SchemaManager(pool)
        sm.table_exists = AsyncMock(return_value=False)
        sm.create_table = AsyncMock(return_value="CREATE TABLE ...")

        template = {
            "value": "person",
            "version": 1,
            "fields": [
                {
                    "name": "photo",
                    "type": "file",
                    "file_config": {"allowed_types": ["image/*"], "max_size_mb": 5.0},
                },
            ],
        }

        await sm.ensure_table_for_template(template)

        call_args = sm.create_table.call_args
        fields = call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("fields")
        assert fields[0].type == FieldType.FILE
        assert fields[0].file_config is not None
        assert fields[0].file_config.max_size_mb == 5.0

    @pytest.mark.asyncio
    async def test_parses_semantic_type_fields(self, mock_pool):
        pool, conn = mock_pool
        sm = SchemaManager(pool)
        sm.table_exists = AsyncMock(return_value=False)
        sm.create_table = AsyncMock(return_value="CREATE TABLE ...")

        template = {
            "value": "location",
            "version": 1,
            "fields": [
                {"name": "coords", "type": "object", "semantic_type": "geo_point"},
            ],
        }

        await sm.ensure_table_for_template(template)

        call_args = sm.create_table.call_args
        fields = call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("fields")
        assert fields[0].semantic_type == SemanticType.GEO_POINT

    @pytest.mark.asyncio
    async def test_uses_custom_table_name_from_reporting_config(self, mock_pool):
        pool, conn = mock_pool
        sm = SchemaManager(pool)
        sm.table_exists = AsyncMock(return_value=False)
        sm.create_table = AsyncMock(return_value="CREATE TABLE ...")

        template = {
            "value": "person",
            "version": 1,
            "fields": [{"name": "name", "type": "string"}],
            "reporting": {"table_name": "my_custom_table"},
        }

        table_name = await sm.ensure_table_for_template(template)
        assert table_name == "my_custom_table"

    @pytest.mark.asyncio
    async def test_handles_array_item_type(self, mock_pool):
        pool, conn = mock_pool
        sm = SchemaManager(pool)
        sm.table_exists = AsyncMock(return_value=False)
        sm.create_table = AsyncMock(return_value="CREATE TABLE ...")

        template = {
            "value": "person",
            "version": 1,
            "fields": [
                {
                    "name": "languages",
                    "type": "array",
                    "array_item_type": "term",
                    "array_terminology_ref": "LANGUAGES",
                },
            ],
        }

        await sm.ensure_table_for_template(template)

        call_args = sm.create_table.call_args
        fields = call_args[0][2] if len(call_args[0]) > 2 else call_args[1].get("fields")
        assert fields[0].type == FieldType.ARRAY
        assert fields[0].array_item_type == FieldType.TERM
        assert fields[0].array_terminology_ref == "LANGUAGES"


# =========================================================================
# DDL Generation - Mixed Fields
# =========================================================================


class TestCreateTableDDLMixedFields:
    """Tests for DDL generation with a realistic mix of field types."""

    def _make_sm(self):
        pool = MagicMock()
        return SchemaManager(pool)

    def test_realistic_person_template(self):
        """DDL for a template with strings, terms, dates, and booleans."""
        sm = self._make_sm()
        fields = [
            TemplateField(name="first_name", type=FieldType.STRING),
            TemplateField(name="last_name", type=FieldType.STRING),
            TemplateField(name="email", type=FieldType.STRING, semantic_type=SemanticType.EMAIL),
            TemplateField(name="country", type=FieldType.TERM),
            TemplateField(name="birth_date", type=FieldType.DATE),
            TemplateField(name="active", type=FieldType.BOOLEAN),
        ]
        ddl = sm.generate_create_table_ddl("person", 1, fields)

        # Verify all field columns are present
        assert '"first_name" TEXT' in ddl
        assert '"last_name" TEXT' in ddl
        assert '"email" TEXT' in ddl  # semantic email -> TEXT
        assert '"country" TEXT' in ddl
        assert '"country_term_id" TEXT' in ddl
        assert '"birth_date" DATE' in ddl
        assert '"active" BOOLEAN' in ddl

        # Verify CREATE TABLE syntax
        assert 'CREATE TABLE IF NOT EXISTS "doc_person"' in ddl
