"""Unit tests for the Registry HashService.

These tests do not require MongoDB or the API -- they test the
hash computation logic in isolation.
"""

import pytest

from registry.services.hash import HashService


class TestDeterministicHashing:
    """Test that hashing is deterministic: same input always produces the same output."""

    def test_same_dict_same_hash(self):
        """Identical dictionaries produce the same hash."""
        key = {"product_id": "PROD-001", "region": "EU"}
        hash1 = HashService.compute_composite_key_hash(key)
        hash2 = HashService.compute_composite_key_hash(key)
        assert hash1 == hash2

    def test_hash_is_sha256_hex(self):
        """Hash output is a valid SHA-256 hex digest (64 hex characters)."""
        key = {"field": "value"}
        h = HashService.compute_composite_key_hash(key)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_keys_different_hash(self):
        """Different dictionaries produce different hashes."""
        h1 = HashService.compute_composite_key_hash({"a": "1"})
        h2 = HashService.compute_composite_key_hash({"a": "2"})
        assert h1 != h2

    def test_different_field_names_different_hash(self):
        """Same value but different field names produce different hashes."""
        h1 = HashService.compute_composite_key_hash({"name": "Alice"})
        h2 = HashService.compute_composite_key_hash({"username": "Alice"})
        assert h1 != h2

    def test_hash_consistency_across_calls(self):
        """Hash is consistent across multiple independent calls."""
        key = {"namespace": "wip", "template_id": "TPL-001", "identity_hash": "abc123"}
        hashes = [HashService.compute_composite_key_hash(key) for _ in range(100)]
        assert len(set(hashes)) == 1


class TestOrderIndependentHashing:
    """Test that key order does not affect the hash."""

    def test_key_order_does_not_matter(self):
        """Dictionaries with the same keys in different order produce the same hash."""
        key1 = {"product_id": "PROD-001", "region": "EU"}
        key2 = {"region": "EU", "product_id": "PROD-001"}
        assert HashService.compute_composite_key_hash(key1) == HashService.compute_composite_key_hash(key2)

    def test_many_keys_order_independent(self):
        """Order independence holds for many keys."""
        key_forward = {"a": "1", "b": "2", "c": "3", "d": "4", "e": "5"}
        key_reverse = {"e": "5", "d": "4", "c": "3", "b": "2", "a": "1"}
        key_mixed = {"c": "3", "a": "1", "e": "5", "b": "2", "d": "4"}
        h_forward = HashService.compute_composite_key_hash(key_forward)
        h_reverse = HashService.compute_composite_key_hash(key_reverse)
        h_mixed = HashService.compute_composite_key_hash(key_mixed)
        assert h_forward == h_reverse == h_mixed

    def test_nested_dict_key_order_independent(self):
        """Order independence extends to nested dictionaries."""
        key1 = {"outer": {"z_key": "z_val", "a_key": "a_val"}}
        key2 = {"outer": {"a_key": "a_val", "z_key": "z_val"}}
        assert HashService.compute_composite_key_hash(key1) == HashService.compute_composite_key_hash(key2)


class TestNestedDictHashing:
    """Test hashing of nested and complex dictionary structures."""

    def test_nested_dict(self):
        """Nested dictionaries are hashed correctly."""
        key = {
            "identity": {
                "first_name": "John",
                "last_name": "Doe",
            },
            "namespace": "wip",
        }
        h = HashService.compute_composite_key_hash(key)
        assert isinstance(h, str)
        assert len(h) == 64

    def test_deeply_nested_dict(self):
        """Deeply nested dictionaries produce deterministic hashes."""
        key = {
            "level1": {
                "level2": {
                    "level3": {
                        "value": "deep"
                    }
                }
            }
        }
        h1 = HashService.compute_composite_key_hash(key)
        h2 = HashService.compute_composite_key_hash(key)
        assert h1 == h2

    def test_list_values(self):
        """Dictionaries containing list values are hashed deterministically."""
        key = {"tags": ["red", "green", "blue"], "name": "palette"}
        h1 = HashService.compute_composite_key_hash(key)
        h2 = HashService.compute_composite_key_hash(key)
        assert h1 == h2

    def test_list_order_matters(self):
        """The order of elements in a list DOES affect the hash."""
        key1 = {"tags": ["red", "green", "blue"]}
        key2 = {"tags": ["blue", "green", "red"]}
        h1 = HashService.compute_composite_key_hash(key1)
        h2 = HashService.compute_composite_key_hash(key2)
        assert h1 != h2

    def test_mixed_value_types(self):
        """Dictionaries with mixed value types (str, int, bool, list, dict) are hashed."""
        key = {
            "name": "Widget",
            "count": 42,
            "active": True,
            "tags": ["a", "b"],
            "nested": {"x": 1},
        }
        h = HashService.compute_composite_key_hash(key)
        assert isinstance(h, str)
        assert len(h) == 64

    def test_numeric_value_type_distinction(self):
        """Integer and string representations of the same number produce different hashes."""
        key_int = {"value": 42}
        key_str = {"value": "42"}
        h_int = HashService.compute_composite_key_hash(key_int)
        h_str = HashService.compute_composite_key_hash(key_str)
        assert h_int != h_str

    def test_boolean_value_type_distinction(self):
        """Boolean and string representations produce different hashes."""
        key_bool = {"active": True}
        key_str = {"active": "true"}
        h_bool = HashService.compute_composite_key_hash(key_bool)
        h_str = HashService.compute_composite_key_hash(key_str)
        assert h_bool != h_str


class TestValueNormalization:
    """Test the normalize_value static method."""

    def test_normalize_none(self):
        """None normalizes to empty string."""
        assert HashService.normalize_value(None) == ""

    def test_normalize_bool_true(self):
        """True normalizes to 'true'."""
        assert HashService.normalize_value(True) == "true"

    def test_normalize_bool_false(self):
        """False normalizes to 'false'."""
        assert HashService.normalize_value(False) == "false"

    def test_normalize_int(self):
        """Integers normalize to their string representation."""
        assert HashService.normalize_value(42) == "42"
        assert HashService.normalize_value(0) == "0"
        assert HashService.normalize_value(-7) == "-7"

    def test_normalize_float(self):
        """Floats normalize to their string representation."""
        assert HashService.normalize_value(3.14) == "3.14"

    def test_normalize_string_strips_and_lowercases(self):
        """Strings are stripped and lowercased."""
        assert HashService.normalize_value("  Hello World  ") == "hello world"
        assert HashService.normalize_value("UPPER") == "upper"
        assert HashService.normalize_value("already_lower") == "already_lower"

    def test_normalize_empty_string(self):
        """Empty string normalizes to empty string."""
        assert HashService.normalize_value("") == ""
        assert HashService.normalize_value("   ") == ""

    def test_normalize_list(self):
        """Lists normalize to sorted JSON."""
        result = HashService.normalize_value([3, 1, 2])
        assert result == "[3,1,2]"  # JSON serialized, not sorted (list order preserved)

    def test_normalize_dict(self):
        """Dicts normalize to JSON with sorted keys."""
        result = HashService.normalize_value({"b": 2, "a": 1})
        assert result == '{"a":1,"b":2}'


class TestEmptyKeyHash:
    """Test hashing of empty and minimal composite keys."""

    def test_empty_dict_hash(self):
        """Empty dictionary produces a valid hash."""
        h = HashService.compute_composite_key_hash({})
        assert isinstance(h, str)
        assert len(h) == 64

    def test_empty_dict_deterministic(self):
        """Empty dictionary always produces the same hash."""
        h1 = HashService.compute_composite_key_hash({})
        h2 = HashService.compute_composite_key_hash({})
        assert h1 == h2

    def test_empty_dict_differs_from_nonempty(self):
        """Empty dictionary hash differs from any non-empty dictionary."""
        h_empty = HashService.compute_composite_key_hash({})
        h_nonempty = HashService.compute_composite_key_hash({"key": "value"})
        assert h_empty != h_nonempty

    def test_single_key_hash(self):
        """Single-key dictionary produces a valid hash."""
        h = HashService.compute_composite_key_hash({"only_key": "only_value"})
        assert isinstance(h, str)
        assert len(h) == 64

    def test_empty_string_values(self):
        """Dictionary with empty string values produces a valid, deterministic hash."""
        key = {"field1": "", "field2": ""}
        h1 = HashService.compute_composite_key_hash(key)
        h2 = HashService.compute_composite_key_hash(key)
        assert h1 == h2
        assert len(h1) == 64

    def test_none_values(self):
        """Dictionary with None values produces a valid hash."""
        key = {"field": None}
        h = HashService.compute_composite_key_hash(key)
        assert isinstance(h, str)
        assert len(h) == 64


class TestHashVerification:
    """Test the verify_hash method."""

    def test_verify_correct_hash(self):
        """verify_hash returns True when hash matches."""
        key = {"product_id": "PROD-001", "region": "EU"}
        expected = HashService.compute_composite_key_hash(key)
        assert HashService.verify_hash(key, expected) is True

    def test_verify_wrong_hash(self):
        """verify_hash returns False when hash does not match."""
        key = {"product_id": "PROD-001", "region": "EU"}
        assert HashService.verify_hash(key, "0" * 64) is False

    def test_verify_empty_key(self):
        """verify_hash works with empty dict."""
        expected = HashService.compute_composite_key_hash({})
        assert HashService.verify_hash({}, expected) is True

    def test_verify_different_key_order(self):
        """verify_hash succeeds regardless of key order."""
        key_original = {"a": "1", "b": "2", "c": "3"}
        expected = HashService.compute_composite_key_hash(key_original)

        key_reordered = {"c": "3", "a": "1", "b": "2"}
        assert HashService.verify_hash(key_reordered, expected) is True

    def test_verify_nested_key(self):
        """verify_hash works with nested dictionaries."""
        key = {"outer": {"inner_b": "2", "inner_a": "1"}}
        expected = HashService.compute_composite_key_hash(key)

        key_reordered = {"outer": {"inner_a": "1", "inner_b": "2"}}
        assert HashService.verify_hash(key_reordered, expected) is True

    def test_verify_modified_value_fails(self):
        """verify_hash returns False when a value is changed."""
        key = {"product_id": "PROD-001"}
        expected = HashService.compute_composite_key_hash(key)

        modified_key = {"product_id": "PROD-002"}
        assert HashService.verify_hash(modified_key, expected) is False

    def test_verify_extra_field_fails(self):
        """verify_hash returns False when an extra field is added."""
        key = {"product_id": "PROD-001"}
        expected = HashService.compute_composite_key_hash(key)

        extended_key = {"product_id": "PROD-001", "extra": "field"}
        assert HashService.verify_hash(extended_key, expected) is False


class TestComputeFieldHash:
    """Test the compute_field_hash method."""

    def test_field_hash_deterministic(self):
        """compute_field_hash is deterministic."""
        h1 = HashService.compute_field_hash("email", "john@example.com")
        h2 = HashService.compute_field_hash("email", "john@example.com")
        assert h1 == h2

    def test_different_fields_different_hash(self):
        """Different field names produce different hashes even with the same value."""
        h1 = HashService.compute_field_hash("email", "john@example.com")
        h2 = HashService.compute_field_hash("username", "john@example.com")
        assert h1 != h2

    def test_field_hash_is_sha256(self):
        """compute_field_hash produces a valid SHA-256 hex digest."""
        h = HashService.compute_field_hash("key", "value")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_field_hash_normalizes_string_value(self):
        """compute_field_hash normalizes string values (strip + lowercase)."""
        h1 = HashService.compute_field_hash("name", "  Alice  ")
        h2 = HashService.compute_field_hash("name", "alice")
        assert h1 == h2

    def test_field_hash_normalizes_none(self):
        """compute_field_hash handles None values."""
        h = HashService.compute_field_hash("field", None)
        assert isinstance(h, str)
        assert len(h) == 64

    def test_field_hash_normalizes_bool(self):
        """compute_field_hash normalizes boolean values."""
        h_true = HashService.compute_field_hash("active", True)
        h_str = HashService.compute_field_hash("active", "true")
        # After normalization, True -> "true" and "true" -> "true", so hashes should match
        assert h_true == h_str


class TestSortDictRecursive:
    """Test the internal _sort_dict_recursive method."""

    def test_flat_dict_sorting(self):
        """Flat dictionary keys are sorted."""
        result = HashService._sort_dict_recursive({"c": 3, "a": 1, "b": 2})
        assert list(result.keys()) == ["a", "b", "c"]

    def test_nested_dict_sorting(self):
        """Nested dictionary keys are sorted recursively."""
        result = HashService._sort_dict_recursive({
            "z": {"b": 2, "a": 1},
            "a": {"d": 4, "c": 3},
        })
        assert list(result.keys()) == ["a", "z"]
        assert list(result["a"].keys()) == ["c", "d"]
        assert list(result["z"].keys()) == ["a", "b"]

    def test_list_values_preserved(self):
        """List values are preserved (not sorted) during recursive sorting."""
        result = HashService._sort_dict_recursive({"tags": [3, 1, 2]})
        assert result["tags"] == [3, 1, 2]

    def test_list_of_dicts_sorted_internally(self):
        """Dictionaries within lists have their keys sorted."""
        result = HashService._sort_dict_recursive({
            "items": [{"b": 2, "a": 1}, {"d": 4, "c": 3}]
        })
        assert list(result["items"][0].keys()) == ["a", "b"]
        assert list(result["items"][1].keys()) == ["c", "d"]

    def test_scalar_passthrough(self):
        """Scalar values pass through unchanged."""
        assert HashService._sort_dict_recursive("hello") == "hello"
        assert HashService._sort_dict_recursive(42) == 42
        assert HashService._sort_dict_recursive(True) is True
        assert HashService._sort_dict_recursive(None) is None
