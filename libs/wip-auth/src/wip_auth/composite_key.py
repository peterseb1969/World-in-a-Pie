"""Composite-key hashing — canonical implementation.

A composite key is what the Registry uses to decide whether two registrations
describe the same entity: ``{ns, value, label}`` for a terminology,
``{ns, terminology_id, value}`` for a term, and so on. Hashing it gives the
Registry a uniform index regardless of the key's shape, and that hash is what
the uniqueness index and every composite-key claim are built on.

This module is a *sibling* of :mod:`wip_auth.document_identity`, and the two
are easy to confuse:

- **document identity hash** — hashes a document's declared identity field
  VALUES, deciding "new version or new document" (PoNIF #3). Namespace-free.
- **composite key hash** (here) — hashes an entity's Registry composite KEY,
  deciding "is this already a registered identity". Namespace-scoped, because
  the key itself carries ``ns``.

Why it lives here rather than in the Registry, where it was born: any service
that has to construct or compare composite keys off the Registry's own write
path needs the exact same bytes. Restore does — a cross-install merge rewrites
the parent IDs embedded in a key and must recompute its hash to match or claim
it. Re-deriving "sort, dump, sha256" at a second call site is precisely how
CASE-316 and CASE-401 produced a hash that looked right and matched nothing;
:mod:`wip_auth.document_identity` exists for the same reason after CASE-402.
One implementation, imported by the Registry and by anyone else who needs it.

The algorithm is exact and case-SENSITIVE. An earlier normalizing variant was
deleted (CASE-568) precisely because it disagreed with this one.
"""

import hashlib
import json
from typing import Any


def _sort_recursive(obj: Any) -> Any:
    """Recursively sort dict keys, walking nested dicts and lists."""
    if isinstance(obj, dict):
        return {k: _sort_recursive(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_sort_recursive(item) for item in obj]
    return obj


def compute_composite_key_hash(composite_key: dict[str, Any]) -> str:
    """SHA-256 hex digest of a composite key.

    Canonical form: keys sorted recursively, then ``json.dumps`` with
    ``sort_keys=True`` and no whitespace, then SHA-256.

    An empty key hashes to the digest of ``{}`` rather than to the empty
    string — callers that mean "this entity opts out of dedup" store an
    empty hash explicitly and must not route through here.
    """
    key_string = json.dumps(
        _sort_recursive(composite_key), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(key_string.encode("utf-8")).hexdigest()


def verify_composite_key_hash(
    composite_key: dict[str, Any], expected_hash: str
) -> bool:
    """True if ``composite_key`` hashes to ``expected_hash``."""
    return compute_composite_key_hash(composite_key) == expected_hash


__all__ = [
    "compute_composite_key_hash",
    "verify_composite_key_hash",
]
