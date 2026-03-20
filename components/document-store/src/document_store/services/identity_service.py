"""Identity service for computing document identity hashes."""

import hashlib
import json
from typing import Any


class IdentityService:
    """
    Service for computing document identity hashes.

    The identity hash is a SHA-256 hash of the document's identity field values,
    computed in a deterministic way (sorted keys, consistent JSON serialization).
    This hash is used to determine if a document is a new entity or an update
    to an existing entity.
    """

    @staticmethod
    def extract_identity_values(
        data: dict[str, Any],
        identity_fields: list[str]
    ) -> dict[str, Any]:
        """
        Extract identity field values from document data.

        Supports nested field paths using dot notation (e.g., 'address.city').

        Args:
            data: Document data
            identity_fields: List of field paths that form the identity

        Returns:
            Dict of field paths to their values

        Raises:
            ValueError: If a required identity field is missing
        """
        identity_values = {}

        for field_path in identity_fields:
            value = IdentityService._get_nested_value(data, field_path)
            if value is None:
                raise ValueError(f"Identity field '{field_path}' is missing or null")
            identity_values[field_path] = value

        return identity_values

    @staticmethod
    def _get_nested_value(data: dict[str, Any], field_path: str) -> Any | None:
        """
        Get a value from nested data using dot notation.

        Args:
            data: Document data
            field_path: Dot-separated path (e.g., 'address.city')

        Returns:
            Value at the path, or None if not found
        """
        parts = field_path.split(".")
        current = data

        for part in parts:
            if not isinstance(current, dict):
                return None
            if part not in current:
                return None
            current = current[part]

        return current

    @staticmethod
    def compute_hash(identity_values: dict[str, Any]) -> str:
        """
        Compute SHA-256 hash of identity values.

        The hash is computed deterministically:
        - Keys are sorted alphabetically
        - Values are JSON-serialized with consistent formatting
        - The resulting string is UTF-8 encoded before hashing

        Args:
            identity_values: Dict of identity field values

        Returns:
            Hex-encoded SHA-256 hash
        """
        # Create deterministic JSON representation
        # Sort keys and use consistent separators
        canonical = json.dumps(
            identity_values,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=str  # Handle datetime and other non-JSON types
        )

        # Compute SHA-256 hash
        hash_bytes = hashlib.sha256(canonical.encode("utf-8")).digest()
        return hash_bytes.hex()

    @staticmethod
    def compute_identity_hash(
        data: dict[str, Any],
        identity_fields: list[str]
    ) -> str:
        """
        Compute the identity hash for document data.

        This is the main entry point that extracts identity values
        and computes their hash.

        Args:
            data: Document data
            identity_fields: List of field paths that form the identity

        Returns:
            Hex-encoded SHA-256 hash

        Raises:
            ValueError: If identity fields are missing or invalid
        """
        if not identity_fields:
            raise ValueError("No identity fields defined for this template")

        identity_values = IdentityService.extract_identity_values(
            data, identity_fields
        )

        return IdentityService.compute_hash(identity_values)

    @staticmethod
    def normalize_value(value: Any) -> Any:
        """
        Normalize a value for consistent hashing.

        - Strings are stripped and lowercased
        - Numbers are kept as-is
        - Booleans are kept as-is
        - Lists/dicts are recursively normalized

        Args:
            value: Value to normalize

        Returns:
            Normalized value
        """
        if isinstance(value, str):
            return value.strip().lower()
        elif isinstance(value, dict):
            return {
                k: IdentityService.normalize_value(v)
                for k, v in value.items()
            }
        elif isinstance(value, list):
            return [IdentityService.normalize_value(v) for v in value]
        else:
            return value

    @staticmethod
    def compute_normalized_hash(
        data: dict[str, Any],
        identity_fields: list[str]
    ) -> str:
        """
        Compute identity hash with normalized values.

        This is useful for case-insensitive identity matching.

        Args:
            data: Document data
            identity_fields: List of field paths that form the identity

        Returns:
            Hex-encoded SHA-256 hash of normalized values
        """
        if not identity_fields:
            raise ValueError("No identity fields defined for this template")

        identity_values = IdentityService.extract_identity_values(
            data, identity_fields
        )

        normalized = {
            k: IdentityService.normalize_value(v)
            for k, v in identity_values.items()
        }

        return IdentityService.compute_hash(normalized)
