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

The resolved-referent feed rule (DECISION-2, 2026-10-02): an identity
field that is term- or reference-typed contributes the canonical ID of
what it RESOLVES TO — a term's ``term_id``, a referenced document's
``document_id`` — never the submitted spelling. Every alias, case
variant or synonym of one referent therefore yields one identity, and a
referent's rename does not fork documents. Resolution decides sameness;
the vocabulary decides case sensitivity. The rule is fail-closed: a
referent identity field whose resolution is missing raises instead of
silently hashing the spelling (which would fork the document later).
The hash algorithm itself (canonical JSON, SHA-256) is unchanged — the
rule changes what FEEDS it. ``referent_identity_fields``,
``build_referent_resolutions``, ``substitute_resolved_referents`` and
``resolved_identity_values`` below implement the rule; external loaders
computing dedup hashes must apply the same feed.
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


# ---------------------------------------------------------------------------
# Resolved-referent identity feed (DECISION-2)
# ---------------------------------------------------------------------------

# Which key in a resolved reference dict carries the referent's canonical ID,
# per reference_type.
_REFERENT_ID_KEYS = {
    "document": "document_id",
    "term": "term_id",
    "terminology": "terminology_id",
    "template": "template_id",
}


def _is_referent_field(field: dict[str, Any]) -> bool:
    """A field whose value names another entity and gets resolved.

    Mirrors the validation collectors exactly: a term field (or term-array
    field) counts only with its terminology ref declared, a reference field
    (or reference-array field) only with its ``reference_type`` declared —
    without those the platform never resolves the value, so the spelling IS
    the value and stays in the feed.
    """
    ftype = field.get("type")
    if ftype == "term":
        return bool(field.get("terminology_ref"))
    if ftype == "reference":
        return bool(field.get("reference_type"))
    if ftype == "array":
        item_type = field.get("array_item_type")
        if item_type == "term":
            return bool(field.get("array_terminology_ref"))
        if item_type == "reference":
            return bool(field.get("reference_type"))
    return False


def referent_identity_fields(
    identity_fields: list[str],
    template_fields: list[dict[str, Any]],
) -> set[str]:
    """The identity fields whose values are referents (term / reference typed).

    These fields fall under the resolved-referent feed rule. Lookup is by the
    identity path's top-level segment, so a dotted identity path into an
    object field never matches here and keeps feeding its submitted value,
    even when it names a term or reference field of the nested template that
    validation resolves. Feeding those resolutions instead would re-hash every
    existing document whose identity uses such a path.
    """
    fields_by_name = {f.get("name"): f for f in template_fields}
    referent: set[str] = set()
    for path in identity_fields:
        field = fields_by_name.get(path.split(".", 1)[0])
        if field is not None and _is_referent_field(field):
            referent.add(path)
    return referent


def build_referent_resolutions(
    term_references: list[dict[str, Any]] | None,
    references: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Map each resolved field path to its referent's canonical ID.

    Accepts the two resolution arrays a validation produces and a document
    stores: ``term_references`` entries (``{field_path, term_id, ...}``) and
    ``references`` entries (``{field_path, reference_type, resolved: {...}}``).
    Array elements appear under their element path (``field[i]``). A term
    reference field populates both arrays with the same ``term_id``, so the
    overlap is harmless; ``references`` entries are applied last and win.
    """
    resolutions: dict[str, Any] = {}
    for entry in term_references or []:
        path = entry.get("field_path")
        term_id = entry.get("term_id")
        if path and term_id:
            resolutions[path] = term_id
    for entry in references or []:
        path = entry.get("field_path")
        id_key = _REFERENT_ID_KEYS.get(entry.get("reference_type") or "")
        resolved = entry.get("resolved") or {}
        referent_id = resolved.get(id_key) if id_key else None
        if path and referent_id:
            resolutions[path] = referent_id
    return resolutions


def substitute_resolved_referents(
    identity_values: dict[str, Any],
    referent_fields: set[str],
    resolutions: dict[str, Any],
) -> dict[str, Any]:
    """Apply the resolved-referent feed rule to extracted identity values.

    Each referent identity field's submitted spelling is replaced by its
    resolution's canonical ID; a list value is replaced element-wise via the
    ``field[i]`` paths. Non-referent fields pass through untouched.

    Fail-closed: a referent identity field (or element) with no resolution
    raises ``ValueError`` — an identity that cannot be resolved is not an
    identity, and hashing the spelling instead would fork the document on a
    later, resolvable write.
    """
    substituted = dict(identity_values)
    for path in referent_fields:
        if path not in substituted:
            continue
        submitted = substituted[path]
        if isinstance(submitted, list):
            resolved_items = []
            for i in range(len(submitted)):
                element_path = f"{path}[{i}]"
                if element_path not in resolutions:
                    raise ValueError(
                        f"Identity field '{element_path}' did not resolve; "
                        "refusing to hash the submitted spelling (resolved-"
                        "referent identity is fail-closed)"
                    )
                resolved_items.append(resolutions[element_path])
            substituted[path] = resolved_items
        else:
            if path not in resolutions:
                raise ValueError(
                    f"Identity field '{path}' did not resolve; refusing to "
                    "hash the submitted spelling (resolved-referent identity "
                    "is fail-closed)"
                )
            substituted[path] = resolutions[path]
    return substituted


def resolved_identity_values(
    data: dict[str, Any],
    identity_fields: list[str],
    template_fields: list[dict[str, Any]],
    term_references: list[dict[str, Any]] | None,
    references: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """One-call feed derivation: extract, then substitute resolved referents.

    This is the full DECISION-2 feed as one step, for callers that hold a
    document plus its stored resolution arrays (integrity checks, restore
    rehash, migration tooling, external loaders). Raises ``ValueError`` for a
    missing identity field (extraction) or an unresolved referent identity
    field (fail-closed substitution).
    """
    values = extract_identity_values(data, identity_fields)
    referent = referent_identity_fields(identity_fields, template_fields)
    if not referent:
        return values
    resolutions = build_referent_resolutions(term_references, references)
    return substitute_resolved_referents(values, referent, resolutions)


__all__ = [
    "build_referent_resolutions",
    "compute_hash",
    "compute_identity_hash",
    "compute_normalized_hash",
    "extract_identity_values",
    "normalize_value",
    "referent_identity_fields",
    "resolved_identity_values",
    "substitute_resolved_referents",
]
