"""Document identity hashing — canonical implementation.

Identity hashing is the mechanism by which WIP decides "is this a new
document or a new version of an existing one" (PoNIF #3). The algorithm
is a contract with four parties:

1. document-store — owns the upsert flow that consumes the hash.
2. External backend services that compute hashes for dedup before submit.
3. External Python tooling (e.g., bulk-import scripts, migration helpers).
4. Documentation readers (docs/data-models.md publishes the algorithm).

Before CASE-402 this lived as ``IdentityService`` inside document-store.
A documentation drift (CASE-401) and a production divergence in an
external loader (CASE-316) demonstrated that re-implementing the
algorithm per call-site silently drifts. This module is the canonical
home; the contract test at ``libs/wip-auth/tests/test_document_identity_contract.py``
pins the algorithm to the digest published in ``docs/data-models.md``
so doc and code stay co-versioned.

NB: this module is a *sibling* of ``identity.py`` (which manages the
request-scoped authenticated *user* identity context). Two unrelated
concepts both called "identity"; the module names disambiguate.
"""

import hashlib
import json
from typing import Any


def _get_nested_value(data: dict[str, Any], field_path: str) -> Any | None:
    """Get a value from nested data using dot notation."""
    parts = field_path.split(".")
    current: Any = data

    for part in parts:
        if not isinstance(current, dict):
            return None
        if part not in current:
            return None
        current = current[part]

    return current


def extract_identity_values(
    data: dict[str, Any],
    identity_fields: list[str],
) -> dict[str, Any]:
    """Extract identity field values from document data.

    Supports nested field paths using dot notation (e.g., ``address.city``).

    Raises ``ValueError`` if any identity field is missing or null.
    """
    identity_values: dict[str, Any] = {}

    for field_path in identity_fields:
        value = _get_nested_value(data, field_path)
        if value is None:
            raise ValueError(f"Identity field '{field_path}' is missing or null")
        identity_values[field_path] = value

    return identity_values


def compute_hash(identity_values: dict[str, Any]) -> str:
    """Compute SHA-256 hash of an already-extracted identity-values dict.

    Canonical JSON: ``sort_keys=True``, no whitespace separators,
    ``ensure_ascii=True``, ``default=str`` (for datetime / UUID).
    """
    canonical = json.dumps(
        identity_values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_identity_hash(
    data: dict[str, Any],
    identity_fields: list[str],
) -> str:
    """Main entry: extract identity values from ``data`` and hash them.

    Raises ``ValueError`` if ``identity_fields`` is empty or any field
    is missing.
    """
    if not identity_fields:
        raise ValueError("No identity fields defined for this template")

    identity_values = extract_identity_values(data, identity_fields)
    return compute_hash(identity_values)


def normalize_value(value: Any) -> Any:
    """Normalize a value for case-insensitive identity matching.

    Strings are stripped + lowercased. Numbers and booleans pass through.
    Dicts and lists are recursively normalized.
    """
    if isinstance(value, str):
        return value.strip().lower()
    if isinstance(value, dict):
        return {k: normalize_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_value(v) for v in value]
    return value


def compute_normalized_hash(
    data: dict[str, Any],
    identity_fields: list[str],
) -> str:
    """Compute identity hash with case-insensitive normalization applied."""
    if not identity_fields:
        raise ValueError("No identity fields defined for this template")

    identity_values = extract_identity_values(data, identity_fields)
    normalized = {k: normalize_value(v) for k, v in identity_values.items()}
    return compute_hash(normalized)


__all__ = [
    "compute_hash",
    "compute_identity_hash",
    "compute_normalized_hash",
    "extract_identity_values",
    "normalize_value",
]
