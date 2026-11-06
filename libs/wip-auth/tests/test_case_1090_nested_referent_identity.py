"""A nested referent identity path feeds its resolved canonical ID too.

Provenance: CASE-1090. The resolved-referent rule held for top-level term and
reference identity fields only: a dotted path into an object field
(``address.country``) fed the submitted spelling, so two aliases of one term
made two documents. The template's ``referent_identity_paths`` (worked out by
the template store) now names nested referent paths; without it, a nested path
cannot be classified and is refused rather than guessed.
"""

from __future__ import annotations

import pytest

from wip_auth.document_identity import (
    compute_hash,
    referent_identity_fields,
    resolved_identity_values,
)

FIELDS = [
    {"name": "name", "type": "string"},
    {"name": "gender", "type": "term", "terminology_ref": "GENDER"},
    {"name": "address", "type": "object", "template_ref": "ADDR", "template_ref_version": 1},
    {"name": "extra", "type": "object"},
]
IDENTITY = ["name", "address.country"]


def test_declared_paths_are_the_answer():
    assert referent_identity_fields(IDENTITY, FIELDS, ["address.country"]) == {"address.country"}
    assert referent_identity_fields(["name", "gender"], FIELDS, ["gender"]) == {"gender"}
    assert referent_identity_fields(IDENTITY, FIELDS, []) == set()


def test_without_declared_paths_top_level_fields_classify_as_before():
    assert referent_identity_fields(["name", "gender"], FIELDS) == {"gender"}


def test_without_declared_paths_a_nested_template_path_is_refused():
    with pytest.raises(ValueError, match=r"address\.country"):
        referent_identity_fields(IDENTITY, FIELDS)


def test_a_path_into_a_free_form_object_is_plain_data():
    assert referent_identity_fields(["name", "extra.code"], FIELDS) == set()


def _values(spelling: str) -> dict:
    data = {"name": "Ada", "address": {"country": spelling}}
    term_refs = [{"field_path": "address.country", "term_id": "term-gb", "value": spelling}]
    return resolved_identity_values(data, IDENTITY, FIELDS, term_refs, [], ["address.country"])


def test_two_aliases_of_one_nested_term_are_one_identity():
    assert _values("GB") == {"name": "Ada", "address.country": "term-gb"}
    assert compute_hash(_values("GB")) == compute_hash(_values("United Kingdom"))


def test_a_nested_referent_without_a_resolution_is_refused():
    data = {"name": "Ada", "address": {"country": "GB"}}
    with pytest.raises(ValueError, match="did not resolve"):
        resolved_identity_values(data, IDENTITY, FIELDS, [], [], ["address.country"])
