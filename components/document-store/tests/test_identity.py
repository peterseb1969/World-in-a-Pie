"""Tests for identity hash computation."""

import pytest

from document_store.services.identity_service import IdentityService


class TestIdentityService:
    """Tests for IdentityService."""

    def test_compute_hash_simple(self):
        """Test hash computation for simple values."""
        values = {"name": "John", "id": "123"}
        hash1 = IdentityService.compute_hash(values)

        assert hash1 is not None
        assert len(hash1) == 64  # SHA-256 produces 64 hex characters

    def test_compute_hash_deterministic(self):
        """Test that hash computation is deterministic."""
        values = {"name": "John", "id": "123"}

        hash1 = IdentityService.compute_hash(values)
        hash2 = IdentityService.compute_hash(values)

        assert hash1 == hash2

    def test_compute_hash_order_independent(self):
        """Test that hash is independent of key order."""
        values1 = {"name": "John", "id": "123"}
        values2 = {"id": "123", "name": "John"}

        hash1 = IdentityService.compute_hash(values1)
        hash2 = IdentityService.compute_hash(values2)

        assert hash1 == hash2

    def test_compute_hash_different_values(self):
        """Test that different values produce different hashes."""
        values1 = {"name": "John", "id": "123"}
        values2 = {"name": "Jane", "id": "123"}

        hash1 = IdentityService.compute_hash(values1)
        hash2 = IdentityService.compute_hash(values2)

        assert hash1 != hash2

    def test_extract_identity_values(self):
        """Test extracting identity values from data."""
        data = {
            "name": "John",
            "id": "123",
            "email": "john@example.com"
        }
        identity_fields = ["name", "id"]

        values = IdentityService.extract_identity_values(data, identity_fields)

        assert values == {"name": "John", "id": "123"}

    def test_extract_identity_values_nested(self):
        """Test extracting nested identity values."""
        data = {
            "person": {
                "name": "John",
                "id": "123"
            },
            "status": "active"
        }
        identity_fields = ["person.name", "person.id"]

        values = IdentityService.extract_identity_values(data, identity_fields)

        assert values == {"person.name": "John", "person.id": "123"}

    def test_extract_identity_values_missing_field(self):
        """Test that missing identity field raises error."""
        data = {
            "name": "John"
        }
        identity_fields = ["name", "id"]

        with pytest.raises(ValueError) as exc_info:
            IdentityService.extract_identity_values(data, identity_fields)

        assert "id" in str(exc_info.value)

    def test_compute_identity_hash(self):
        """Test computing identity hash from data."""
        data = {
            "national_id": "123456789",
            "name": "John",
            "extra": "ignored"
        }
        identity_fields = ["national_id"]

        hash_value = IdentityService.compute_identity_hash(data, identity_fields)

        assert hash_value is not None
        assert len(hash_value) == 64

    def test_compute_identity_hash_empty_fields(self):
        """Test that empty identity fields raise error."""
        data = {"name": "John"}

        with pytest.raises(ValueError):
            IdentityService.compute_identity_hash(data, [])

    def test_normalize_value_string(self):
        """Test normalizing string values."""
        assert IdentityService.normalize_value("  JOHN  ") == "john"
        assert IdentityService.normalize_value("Hello World") == "hello world"

    def test_normalize_value_preserves_numbers(self):
        """Test that numbers are preserved."""
        assert IdentityService.normalize_value(123) == 123
        assert IdentityService.normalize_value(12.5) == 12.5

    def test_normalize_value_nested(self):
        """Test normalizing nested structures."""
        value = {
            "name": "JOHN",
            "tags": ["TAG1", "TAG2"]
        }

        normalized = IdentityService.normalize_value(value)

        assert normalized["name"] == "john"
        assert normalized["tags"] == ["tag1", "tag2"]

    def test_compute_normalized_hash(self):
        """Test computing normalized hash for case-insensitive matching."""
        data1 = {"name": "John", "id": "ABC123"}
        data2 = {"name": "JOHN", "id": "abc123"}
        identity_fields = ["name", "id"]

        hash1 = IdentityService.compute_normalized_hash(data1, identity_fields)
        hash2 = IdentityService.compute_normalized_hash(data2, identity_fields)

        assert hash1 == hash2

    def test_get_nested_value(self):
        """Test getting nested values."""
        data = {
            "level1": {
                "level2": {
                    "value": "deep"
                }
            }
        }

        assert IdentityService._get_nested_value(data, "level1.level2.value") == "deep"
        assert IdentityService._get_nested_value(data, "level1.level2") == {"value": "deep"}
        assert IdentityService._get_nested_value(data, "missing") is None
        assert IdentityService._get_nested_value(data, "level1.missing") is None

    def test_hash_with_special_characters(self):
        """Test hash computation with special characters."""
        values = {
            "name": "John O'Brien",
            "email": "john+test@example.com",
            "path": "/usr/local/bin"
        }

        hash_value = IdentityService.compute_hash(values)
        assert hash_value is not None
        assert len(hash_value) == 64

    def test_hash_with_unicode(self):
        """Test hash computation with unicode characters."""
        values = {
            "name": "Jöhn Döe",
            "city": "北京",
            "emoji": "Hello 👋"
        }

        hash_value = IdentityService.compute_hash(values)
        assert hash_value is not None
        assert len(hash_value) == 64
