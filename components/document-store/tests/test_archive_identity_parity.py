"""wip-archive's offline identity code is a verbatim copy of wip-auth's.

`wip_archive.identity` cannot import `wip_auth` (its `__init__` pulls in
FastAPI), so it carries a copy of the identity feed and the hash the Registry
stores. A copy is how this algorithm drifted before, so this suite holds the two
together: every function must have identical source on both sides, the copy
must carry nothing extra, and both must produce the same hashes on the same
inputs. It lives here because document-store is the one suite where both
packages are importable.
"""

from __future__ import annotations

import inspect
from types import ModuleType

from wip_archive import identity as archive_identity

import wip_auth.composite_key as auth_composite_key
import wip_auth.document_identity as auth_identity


def _functions(module: ModuleType) -> dict[str, object]:
    return {
        name: obj
        for name, obj in inspect.getmembers(module, inspect.isfunction)
        if obj.__module__ == module.__name__
    }


# Everything document_identity defines, plus the composite-key hash the
# Registry stores as identity_hash (and its helper).
ORIGINALS = {
    **_functions(auth_identity),
    "_sort_recursive": auth_composite_key._sort_recursive,
    "compute_composite_key_hash": auth_composite_key.compute_composite_key_hash,
}


def test_every_function_is_copied_verbatim() -> None:
    copied = _functions(archive_identity)
    for name, original in ORIGINALS.items():
        assert name in copied, f"wip_archive.identity lacks {name}: copy it from wip_auth"
        assert inspect.getsource(copied[name]) == inspect.getsource(original), (
            f"{name} differs between wip_auth and wip_archive.identity — "
            "change the original, then copy it verbatim"
        )


def test_the_copy_carries_nothing_extra() -> None:
    extra = set(_functions(archive_identity)) - set(ORIGINALS)
    assert not extra, f"wip_archive.identity defines functions wip_auth lacks: {sorted(extra)}"


TEMPLATE = {
    "identity_fields": ["code", "colour", "owner", "tags"],
    "fields": [
        {"name": "code", "type": "string"},
        {"name": "colour", "type": "term", "terminology_ref": "COLOUR"},
        {"name": "owner", "type": "reference", "reference_type": "document"},
        {"name": "tags", "type": "array", "array_item_type": "term",
         "array_terminology_ref": "TAG"},
    ],
}
DATA = {"code": "A-1", "colour": "Red", "owner": "OWNER-7", "tags": ["x", "y"]}
TERM_REFERENCES = [
    {"field_path": "colour", "term_id": "term-red"},
    {"field_path": "tags[0]", "term_id": "term-x"},
    {"field_path": "tags[1]", "term_id": "term-y"},
]
REFERENCES = [
    {"field_path": "owner", "reference_type": "document",
     "resolved": {"document_id": "doc-owner"}},
]


def test_both_resolve_the_same_identity_values() -> None:
    # Referents feed their resolved ids, never the submitted spelling.
    args = (DATA, TEMPLATE["identity_fields"], TEMPLATE["fields"], TERM_REFERENCES, REFERENCES)
    assert archive_identity.resolved_identity_values(*args) == (
        auth_identity.resolved_identity_values(*args)
    )


def test_both_hash_identically() -> None:
    values = auth_identity.resolved_identity_values(
        DATA, TEMPLATE["identity_fields"], TEMPLATE["fields"], TERM_REFERENCES, REFERENCES
    )
    assert archive_identity.compute_composite_key_hash(values) == (
        auth_composite_key.compute_composite_key_hash(values)
    )
    assert archive_identity.compute_hash(values) == auth_identity.compute_hash(values)
    # For JSON-native values the stored hash and compute_hash agree.
    assert archive_identity.compute_composite_key_hash(values) == (
        archive_identity.compute_hash(values)
    )
