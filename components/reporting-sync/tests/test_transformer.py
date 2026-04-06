"""
Tests for the document transformer and schema manager.
"""

import json
from unittest.mock import MagicMock

from reporting_sync.models import (
    FieldType,
    ReportingConfig,
    SyncStrategy,
    TemplateField,
)
from reporting_sync.schema_manager import SchemaManager
from reporting_sync.transformer import DocumentTransformer


def _make_template(*field_defs: tuple[str, str]) -> dict:
    """Build a minimal template dict for tests.

    Each positional arg is (name, type), e.g. ("email", "string").
    """
    return {
        "fields": [
            {"name": name, "type": ftype, "label": name}
            for name, ftype in field_defs
        ],
    }


class TestDocumentTransformer:
    """Tests for DocumentTransformer."""

    def test_simple_document(self):
        """Test transforming a simple document."""
        transformer = DocumentTransformer()
        template = _make_template(
            ("first_name", "string"), ("last_name", "string"), ("email", "string"),
        )

        document = {
            "document_id": "doc-123",
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc123",
            "namespace": "wip",
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "test-user",
            "data": {
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
            },
            "term_references": {},
        }

        rows = transformer.transform(document, template)

        assert len(rows) == 1
        row = rows[0]
        assert row["document_id"] == "doc-123"
        assert row["first_name"] == "John"
        assert row["last_name"] == "Doe"
        assert row["email"] == "john@example.com"

    def test_nested_object(self):
        """Test transforming a document with nested objects."""
        transformer = DocumentTransformer()
        template = _make_template(("name", "string"), ("address", "object"))

        document = {
            "document_id": "doc-123",
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc123",
            "namespace": "wip",
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "test-user",
            "data": {
                "name": "John",
                "address": {
                    "street": "123 Main St",
                    "city": "New York",
                    "country": "USA",
                },
            },
            "term_references": {},
        }

        rows = transformer.transform(document, template)

        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "John"
        # Nested objects are now stored as JSON by default (flatten_nested=False)
        address = json.loads(row["address"])
        assert address["street"] == "123 Main St"
        assert address["city"] == "New York"
        assert address["country"] == "USA"

    def test_term_references(self):
        """Test transforming a document with term references."""
        transformer = DocumentTransformer()
        template = _make_template(
            ("name", "string"), ("gender", "term"), ("country", "term"),
        )

        document = {
            "document_id": "doc-123",
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc123",
            "namespace": "wip",
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "test-user",
            "data": {
                "name": "John",
                "gender": "Male",
                "country": "USA",
            },
            "term_references": [
                {"field_path": "gender", "term_id": "0190b000-0000-7000-0000-000000000001"},
                {"field_path": "country", "term_id": "0190b000-0000-7000-0000-000000000042"},
            ],
        }

        rows = transformer.transform(document, template)

        assert len(rows) == 1
        row = rows[0]
        assert row["gender"] == "Male"
        assert row["gender_term_id"] == "0190b000-0000-7000-0000-000000000001"
        assert row["country"] == "USA"
        assert row["country_term_id"] == "0190b000-0000-7000-0000-000000000042"

    def test_array_flattening(self):
        """Test that arrays with term references are stored as JSON.

        Array expansion is currently disabled (_expand_arrays returns [base_row]).
        Arrays are stored as JSON columns, and term_references use the array format.
        """
        config = ReportingConfig(flatten_arrays=True)
        transformer = DocumentTransformer(config)
        template = _make_template(("name", "string"), ("languages", "array"))

        document = {
            "document_id": "doc-123",
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc123",
            "namespace": "wip",
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "test-user",
            "data": {
                "name": "John",
                "languages": ["English", "Spanish", "French"],
            },
            "term_references": [
                {"field_path": "languages[0]", "term_id": "0190b000-0000-7000-0000-000000000001"},
                {"field_path": "languages[1]", "term_id": "0190b000-0000-7000-0000-000000000002"},
                {"field_path": "languages[2]", "term_id": "0190b000-0000-7000-0000-000000000003"},
            ],
        }

        rows = transformer.transform(document, template)

        # Array expansion is disabled — single row with JSON arrays
        assert len(rows) == 1
        assert rows[0]["name"] == "John"
        languages = json.loads(rows[0]["languages"])
        assert languages == ["English", "Spanish", "French"]

    def test_array_no_flatten(self):
        """Test that arrays are stored as JSON when flatten_arrays is False."""
        config = ReportingConfig(flatten_arrays=False)
        transformer = DocumentTransformer(config)
        template = _make_template(("name", "string"), ("languages", "array"))

        document = {
            "document_id": "doc-123",
            "template_id": "0190c000-0000-7000-0000-000000000001",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc123",
            "namespace": "wip",
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "test-user",
            "data": {
                "name": "John",
                "languages": ["English", "Spanish"],
            },
            "term_references": {},
        }

        rows = transformer.transform(document, template)

        assert len(rows) == 1
        assert rows[0]["languages"] == '["English", "Spanish"]'

    def test_string_field_with_datetime_value_stays_string(self):
        """CASE-22: A string-typed field containing an ISO datetime must not be
        converted to a Python datetime — it must remain a str so asyncpg can
        bind it to the TEXT column the schema_manager created."""
        transformer = DocumentTransformer()
        template = _make_template(
            ("sync_key", "string"), ("last_sync", "string"),
        )

        document = {
            "document_id": "doc-sync",
            "template_id": "TPL-SYNC",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc",
            "namespace": "clintrial",
            "created_at": "2026-04-06T15:00:00Z",
            "created_by": "import",
            "data": {
                "sync_key": "clintrial-import",
                "last_sync": "2026-04-06T15:38:38.749Z",
            },
            "term_references": [],
        }

        rows = transformer.transform(document, template)
        row = rows[0]

        # last_sync must be a plain string, NOT a datetime object
        assert isinstance(row["last_sync"], str)
        assert row["last_sync"] == "2026-04-06T15:38:38.749Z"

    def test_datetime_field_converted_to_python_datetime(self):
        """A datetime-typed field should be converted to a Python datetime."""
        from datetime import datetime

        transformer = DocumentTransformer()
        template = _make_template(
            ("name", "string"), ("birth_date", "datetime"),
        )

        document = {
            "document_id": "doc-dt",
            "template_id": "TPL-DT",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc",
            "namespace": "wip",
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "test",
            "data": {
                "name": "Alice",
                "birth_date": "1990-05-15T08:30:00Z",
            },
            "term_references": [],
        }

        rows = transformer.transform(document, template)
        row = rows[0]

        assert isinstance(row["birth_date"], datetime)
        assert row["name"] == "Alice"

    def test_date_field_converted_to_python_date(self):
        """A date-typed field should be converted to a Python date."""
        from datetime import date

        transformer = DocumentTransformer()
        template = _make_template(
            ("name", "string"), ("hire_date", "date"),
        )

        document = {
            "document_id": "doc-d",
            "template_id": "TPL-D",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc",
            "namespace": "wip",
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "test",
            "data": {
                "name": "Bob",
                "hire_date": "2024-03-01",
            },
            "term_references": [],
        }

        rows = transformer.transform(document, template)
        row = rows[0]

        assert isinstance(row["hire_date"], date)
        assert row["name"] == "Bob"

    def test_upsert_sql_generation_latest_only(self):
        """Test UPSERT SQL generation for latest_only strategy."""
        transformer = DocumentTransformer()

        row = {
            "document_id": "doc-123",
            "version": 1,
            "status": "active",
            "name": "John",
        }

        sql, values = transformer.generate_upsert_sql("doc_person", row, "latest_only")

        assert "INSERT INTO" in sql
        assert "ON CONFLICT (document_id)" in sql
        assert "DO UPDATE SET" in sql
        assert "WHERE" in sql
        # latest_only uses version comparison for conditional update
        assert "version < EXCLUDED.version" in sql
        assert values == ["doc-123", 1, "active", "John"]

    def test_upsert_sql_generation_all_versions(self):
        """Test INSERT SQL generation for all_versions strategy uses composite PK."""
        transformer = DocumentTransformer()

        row = {
            "document_id": "doc-123",
            "version": 1,
            "status": "active",
            "name": "John",
        }

        sql, values = transformer.generate_upsert_sql("doc_person", row, "all_versions")

        assert "INSERT INTO" in sql
        # all_versions uses composite PK (document_id, version) for conflict
        assert "ON CONFLICT (document_id, version) DO NOTHING" in sql
        # Should NOT have DO UPDATE or single-column conflict
        assert "DO UPDATE SET" not in sql
        assert values == ["doc-123", 1, "active", "John"]


class TestSchemaManagerDDL:
    """Tests for SchemaManager DDL generation."""

    def _make_schema_manager(self):
        """Create a SchemaManager with a mock pool."""
        pool = MagicMock()
        return SchemaManager(pool)

    def test_all_versions_composite_pk(self):
        """Test that all_versions strategy generates composite PK (document_id, version)."""
        sm = self._make_schema_manager()
        config = ReportingConfig(sync_strategy=SyncStrategy.ALL_VERSIONS)
        fields = [
            TemplateField(name="name", type=FieldType.STRING),
        ]

        ddl = sm.generate_create_table_ddl("person", 1, fields, config)

        # Should have composite primary key
        assert "PRIMARY KEY (document_id, version)" in ddl
        # document_id column should NOT have inline PRIMARY KEY
        assert "document_id\" TEXT NOT NULL" in ddl
        assert "document_id\" TEXT PRIMARY KEY" not in ddl

    def test_latest_only_single_pk(self):
        """Test that latest_only strategy generates single-column PK on document_id."""
        sm = self._make_schema_manager()
        config = ReportingConfig(sync_strategy=SyncStrategy.LATEST_ONLY)
        fields = [
            TemplateField(name="name", type=FieldType.STRING),
        ]

        ddl = sm.generate_create_table_ddl("person", 1, fields, config)

        # Should have single-column primary key inline
        assert "document_id\" TEXT PRIMARY KEY" in ddl
        # Should NOT have composite primary key constraint
        assert "PRIMARY KEY (document_id, version)" not in ddl

    def test_all_versions_no_active_identity_index(self):
        """Test that all_versions strategy does NOT create the partial unique index."""
        sm = self._make_schema_manager()
        config = ReportingConfig(sync_strategy=SyncStrategy.ALL_VERSIONS)
        fields = [
            TemplateField(name="name", type=FieldType.STRING),
        ]

        ddl = sm.generate_create_table_ddl("person", 1, fields, config)

        # Partial unique index should NOT exist for all_versions
        assert "_ns_active_identity_idx" not in ddl

    def test_latest_only_has_active_identity_index(self):
        """Test that latest_only strategy creates the partial unique index."""
        sm = self._make_schema_manager()
        config = ReportingConfig(sync_strategy=SyncStrategy.LATEST_ONLY)
        fields = [
            TemplateField(name="name", type=FieldType.STRING),
        ]

        ddl = sm.generate_create_table_ddl("person", 1, fields, config)

        # Partial unique index should exist for latest_only
        assert "_ns_active_identity_idx" in ddl
        assert "WHERE status = 'active'" in ddl

    def test_default_strategy_is_latest_only(self):
        """Test that default config (no explicit strategy) uses latest_only behavior."""
        sm = self._make_schema_manager()
        # No config means default ReportingConfig which is latest_only
        fields = [
            TemplateField(name="email", type=FieldType.STRING),
        ]

        ddl = sm.generate_create_table_ddl("contact", 1, fields)

        # Default should be latest_only: single-column PK
        assert "document_id\" TEXT PRIMARY KEY" in ddl
        assert "PRIMARY KEY (document_id, version)" not in ddl
        # Should have partial unique index
        assert "_ns_active_identity_idx" in ddl
