"""Hash computation service for composite keys."""

import hashlib
import json
from typing import Any


class HashService:
    """Service for computing deterministic hashes of composite keys."""

    @staticmethod
    def compute_composite_key_hash(composite_key: dict[str, Any]) -> str:
        """
        Compute a deterministic SHA-256 hash for a composite key.

        Algorithm:
        1. Sort dictionary keys recursively
        2. Serialize to JSON with sorted keys
        3. Compute SHA-256 hash
        4. Return hex digest

        Args:
            composite_key: Dictionary of key-value pairs

        Returns:
            SHA-256 hex digest string
        """
        # Recursively sort the dictionary
        sorted_key = HashService._sort_dict_recursive(composite_key)

        # Serialize to JSON with sorted keys and no extra whitespace
        key_string = json.dumps(sorted_key, sort_keys=True, separators=(',', ':'))

        # Compute and return SHA-256 hash
        return hashlib.sha256(key_string.encode('utf-8')).hexdigest()

    @staticmethod
    def _sort_dict_recursive(obj: Any) -> Any:
        """
        Recursively sort dictionary keys.

        Handles nested dictionaries and lists.
        """
        if isinstance(obj, dict):
            return {k: HashService._sort_dict_recursive(v) for k, v in sorted(obj.items())}
        elif isinstance(obj, list):
            return [HashService._sort_dict_recursive(item) for item in obj]
        else:
            return obj

    # CASE-568: normalize_value + compute_field_hash removed — no production
    # callers, and their case-insensitive normalization disagreed with the
    # live compute_composite_key_hash (exact-value, case-sensitive). Reviving
    # them would have silently hashed differently than every stored key.

    @staticmethod
    def verify_hash(composite_key: dict[str, Any], expected_hash: str) -> bool:
        """
        Verify that a composite key matches an expected hash.

        Args:
            composite_key: Dictionary of key-value pairs
            expected_hash: Expected SHA-256 hex digest

        Returns:
            True if the computed hash matches the expected hash
        """
        computed = HashService.compute_composite_key_hash(composite_key)
        return computed == expected_hash
