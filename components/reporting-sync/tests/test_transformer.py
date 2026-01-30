"""
Tests for the document transformer.
"""

import pytest
from reporting_sync.transformer import DocumentTransformer
from reporting_sync.models import ReportingConfig


class TestDocumentTransformer:
    """Tests for DocumentTransformer."""

    def test_simple_document(self):
        """Test transforming a simple document."""
        transformer = DocumentTransformer()

        document = {
            "document_id": "doc-123",
            "template_id": "TPL-000001",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc123",
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "test-user",
            "data": {
                "first_name": "John",
                "last_name": "Doe",
                "email": "john@example.com",
            },
            "term_references": {},
        }

        rows = transformer.transform(document)

        assert len(rows) == 1
        row = rows[0]
        assert row["document_id"] == "doc-123"
        assert row["first_name"] == "John"
        assert row["last_name"] == "Doe"
        assert row["email"] == "john@example.com"

    def test_nested_object(self):
        """Test transforming a document with nested objects."""
        transformer = DocumentTransformer()

        document = {
            "document_id": "doc-123",
            "template_id": "TPL-000001",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc123",
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

        rows = transformer.transform(document)

        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "John"
        assert row["address_street"] == "123 Main St"
        assert row["address_city"] == "New York"
        assert row["address_country"] == "USA"

    def test_term_references(self):
        """Test transforming a document with term references."""
        transformer = DocumentTransformer()

        document = {
            "document_id": "doc-123",
            "template_id": "TPL-000001",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc123",
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "test-user",
            "data": {
                "name": "John",
                "gender": "Male",
                "country": "USA",
            },
            "term_references": {
                "gender": "T-000001",
                "country": "T-000042",
            },
        }

        rows = transformer.transform(document)

        assert len(rows) == 1
        row = rows[0]
        assert row["gender"] == "Male"
        assert row["gender_term_id"] == "T-000001"
        assert row["country"] == "USA"
        assert row["country_term_id"] == "T-000042"

    def test_array_flattening(self):
        """Test that arrays are flattened into multiple rows."""
        config = ReportingConfig(flatten_arrays=True)
        transformer = DocumentTransformer(config)

        document = {
            "document_id": "doc-123",
            "template_id": "TPL-000001",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc123",
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "test-user",
            "data": {
                "name": "John",
                "languages": ["English", "Spanish", "French"],
            },
            "term_references": {
                "languages": ["T-001", "T-002", "T-003"],
            },
        }

        rows = transformer.transform(document)

        assert len(rows) == 3
        assert rows[0]["languages"] == "English"
        assert rows[0]["languages_term_id"] == "T-001"
        assert rows[1]["languages"] == "Spanish"
        assert rows[1]["languages_term_id"] == "T-002"
        assert rows[2]["languages"] == "French"
        assert rows[2]["languages_term_id"] == "T-003"

    def test_array_no_flatten(self):
        """Test that arrays are stored as JSON when flatten_arrays is False."""
        config = ReportingConfig(flatten_arrays=False)
        transformer = DocumentTransformer(config)

        document = {
            "document_id": "doc-123",
            "template_id": "TPL-000001",
            "template_version": 1,
            "version": 1,
            "status": "active",
            "identity_hash": "abc123",
            "created_at": "2024-01-30T10:00:00Z",
            "created_by": "test-user",
            "data": {
                "name": "John",
                "languages": ["English", "Spanish"],
            },
            "term_references": {},
        }

        rows = transformer.transform(document)

        assert len(rows) == 1
        assert rows[0]["languages"] == '["English", "Spanish"]'

    def test_upsert_sql_generation(self):
        """Test UPSERT SQL generation."""
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
        assert values == ["doc-123", 1, "active", "John"]
