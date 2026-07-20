"""Hash computation service for composite keys.

The algorithm itself moved to :mod:`wip_auth.composite_key`, which is now its
canonical home — restore has to construct and compare composite keys off the
Registry's write path (a cross-install merge rewrites the parent IDs embedded
in a key and must recompute its hash), and a second hand-rolled "sort, dump,
sha256" is exactly how a hash that looks right comes to match nothing.

This class stays as the Registry's in-house name for it: it has many call
sites, and the indirection is one line each. New callers, inside the Registry
or out, should import from ``wip_auth.composite_key`` directly.
"""

from typing import Any

from wip_auth.composite_key import (
    compute_composite_key_hash as _compute_composite_key_hash,
)
from wip_auth.composite_key import (
    verify_composite_key_hash as _verify_composite_key_hash,
)


class HashService:
    """Service for computing deterministic hashes of composite keys."""

    @staticmethod
    def compute_composite_key_hash(composite_key: dict[str, Any]) -> str:
        """Deterministic SHA-256 hex digest for a composite key.

        Keys sorted recursively, serialized with sorted keys and no
        whitespace, then SHA-256. Exact and case-sensitive: a normalizing
        variant was deleted in CASE-568 because it disagreed with this one.
        """
        return _compute_composite_key_hash(composite_key)

    @staticmethod
    def verify_hash(composite_key: dict[str, Any], expected_hash: str) -> bool:
        """True if ``composite_key`` hashes to ``expected_hash``."""
        return _verify_composite_key_hash(composite_key, expected_hash)
