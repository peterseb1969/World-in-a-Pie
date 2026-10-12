"""Contract tests for wip_auth.document_identity.

The whole point of CASE-402 is that the algorithm published in
``docs/data-models.md`` must match the algorithm executed by the platform.
The digest below is the worked example printed in that doc. If the
algorithm drifts, this test fails AND the doc must be re-published in the
same change. Doc and code stay co-versioned by construction.
"""

import pytest

from wip_auth.document_identity import (
    build_referent_resolutions,
    compute_hash,
    compute_identity_hash,
    compute_normalized_hash,
    extract_identity_values,
    normalize_value,
    referent_identity_fields,
    resolved_identity_values,
    substitute_resolved_referents,
)

# Published in docs/data-models.md, Identity Hash Algorithm section.
# The pair (input, expected) is the doc's worked example.
DOC_WORKED_INPUT = {"first_name": "Alice", "email": "alice@example.com"}
DOC_WORKED_IDENTITY_FIELDS = ["email"]
DOC_WORKED_DIGEST = "9327398c303b9282f3826f9d3a65a17c63d720e4ce06651dad2b3edfa2892697"


class TestPublishedDigestContract:
    """Anchors the algorithm to the doc's worked example."""

    def test_doc_worked_example_matches_published_digest(self):
        digest = compute_identity_hash(
            DOC_WORKED_INPUT,
            DOC_WORKED_IDENTITY_FIELDS,
        )
        assert digest == DOC_WORKED_DIGEST, (
            "Identity hash diverged from docs/data-models.md. Either the "
            "algorithm changed (update the doc to match) or the doc was "
            "wrong (update DOC_WORKED_DIGEST here). Don't silently change "
            "one without the other."
        )

    def test_extract_identity_values_returns_dotted_keys(self):
        values = extract_identity_values(
            {"a": 1, "b": {"c": 2}}, ["a", "b.c"],
        )
        assert values == {"a": 1, "b.c": 2}

    def test_compute_hash_is_deterministic_under_key_reordering(self):
        h1 = compute_hash({"a": 1, "b": 2})
        h2 = compute_hash({"b": 2, "a": 1})
        assert h1 == h2


class TestNormalizedHashContract:
    """compute_normalized_hash strips + lowercases strings recursively."""

    def test_case_insensitive_match(self):
        upper = {"email": "ALICE@example.com"}
        lower = {"email": "alice@example.com"}
        assert (
            compute_normalized_hash(upper, ["email"])
            == compute_normalized_hash(lower, ["email"])
        )

    def test_strict_hash_distinguishes_case(self):
        upper = {"email": "ALICE@example.com"}
        lower = {"email": "alice@example.com"}
        assert (
            compute_identity_hash(upper, ["email"])
            != compute_identity_hash(lower, ["email"])
        )

    def test_normalize_value_recursive(self):
        assert normalize_value("  Hello  ") == "hello"
        assert normalize_value(
            {"k": "  Foo  ", "n": [{"v": " BAR "}]}
        ) == {"k": "foo", "n": [{"v": "bar"}]}
        assert normalize_value(42) == 42
        assert normalize_value(True) is True


# ---------------------------------------------------------------------------
# Resolved-referent feed (DECISION-2)
# ---------------------------------------------------------------------------
#
# A term- or reference-typed identity field contributes the canonical ID of
# what it resolves to, never the submitted spelling. Published in
# docs/data-models.md next to the algorithm; the worked example below is the
# doc's worked example for the feed rule, pinned the same way as the digest.

RESOLVED_FEED_TEMPLATE_FIELDS = [
    {"name": "app", "type": "term", "terminology_ref": "KB_APP"},
    {"name": "title", "type": "string"},
    {
        "name": "parent",
        "type": "reference",
        "reference_type": "document",
        "target_templates": ["PROJECT"],
    },
]

# Two spellings of the same term resolve to one term_id → one digest.
RESOLVED_FEED_TERM_ID = "01999999-0000-7000-8000-000000000001"
RESOLVED_FEED_DIGEST = (
    # compute_hash({"app": RESOLVED_FEED_TERM_ID}) — the doc's worked example.
    "73c1010376586438c3e0ea72e858ea2cfe0637b3caedc8d1798ae475ac684f98"
)


class TestResolvedReferentFeed:
    """The DECISION-2 feed rule, pinned."""

    def test_referent_fields_cover_term_and_reference_types(self):
        fields = [
            {"name": "app", "type": "term", "terminology_ref": "KB_APP"},
            {"name": "title", "type": "string"},
            {"name": "parent", "type": "reference", "reference_type": "document"},
            {"name": "tags", "type": "array", "array_item_type": "term",
             "array_terminology_ref": "TAGS"},
            {"name": "links", "type": "array", "array_item_type": "reference",
             "reference_type": "document"},
            # Unresolvable shapes stay out of the rule: the platform never
            # resolves them, so the spelling IS the value.
            {"name": "loose_term", "type": "term"},
            {"name": "loose_ref", "type": "reference"},
        ]
        identity_fields = [
            "app", "title", "parent", "tags", "links", "loose_term",
            "loose_ref", "address.city",
        ]
        assert referent_identity_fields(identity_fields, fields) == {
            "app", "parent", "tags", "links",
        }

    def test_aliases_of_one_term_yield_one_digest(self):
        fields = RESOLVED_FEED_TEMPLATE_FIELDS
        term_refs = [{"field_path": "app", "term_id": RESOLVED_FEED_TERM_ID}]
        canonical = resolved_identity_values(
            {"app": "ClinTrial"}, ["app"], fields, term_refs, None,
        )
        alias = resolved_identity_values(
            {"app": "clintrial"}, ["app"], fields, term_refs, None,
        )
        assert canonical == alias == {"app": RESOLVED_FEED_TERM_ID}
        assert compute_hash(canonical) == RESOLVED_FEED_DIGEST

    def test_reference_identity_field_feeds_the_referent_document_id(self):
        refs = [{
            "field_path": "parent",
            "reference_type": "document",
            "resolved": {"document_id": "01888888-0000-7000-8000-000000000002"},
        }]
        values = resolved_identity_values(
            {"parent": "PROJECT-ALPHA", "title": "x"},
            ["parent", "title"],
            RESOLVED_FEED_TEMPLATE_FIELDS,
            None,
            refs,
        )
        assert values == {
            "parent": "01888888-0000-7000-8000-000000000002",
            "title": "x",
        }

    def test_array_identity_field_substitutes_element_wise(self):
        fields = [{"name": "tags", "type": "array", "array_item_type": "term",
                   "array_terminology_ref": "TAGS"}]
        term_refs = [
            {"field_path": "tags[0]", "term_id": "T-0"},
            {"field_path": "tags[1]", "term_id": "T-1"},
        ]
        values = resolved_identity_values(
            {"tags": ["Alpha", "beta"]}, ["tags"], fields, term_refs, None,
        )
        assert values == {"tags": ["T-0", "T-1"]}

    def test_fail_closed_on_missing_resolution(self):
        with pytest.raises(ValueError, match="fail-closed"):
            resolved_identity_values(
                {"app": "ClinTrial"},
                ["app"],
                RESOLVED_FEED_TEMPLATE_FIELDS,
                None,  # no resolutions — e.g. Def-Store outage
                None,
            )

    def test_fail_closed_on_partially_resolved_array(self):
        fields = [{"name": "tags", "type": "array", "array_item_type": "term",
                   "array_terminology_ref": "TAGS"}]
        term_refs = [{"field_path": "tags[0]", "term_id": "T-0"}]
        with pytest.raises(ValueError, match=r"tags\[1\]"):
            resolved_identity_values(
                {"tags": ["Alpha", "beta"]}, ["tags"], fields, term_refs, None,
            )

    def test_non_referent_fields_pass_through_unchanged(self):
        substituted = substitute_resolved_referents(
            {"email": "alice@example.com"}, set(), {},
        )
        assert substituted == {"email": "alice@example.com"}

    def test_references_entry_wins_over_term_references_on_same_path(self):
        resolutions = build_referent_resolutions(
            [{"field_path": "app", "term_id": "T-legacy"}],
            [{"field_path": "app", "reference_type": "term",
              "resolved": {"term_id": "T-ref"}}],
        )
        assert resolutions == {"app": "T-ref"}


# Note: cross-component parity (IdentityService delegates byte-identically)
# is verified by document-store's existing test_identity.py suite — the
# wip-auth test runner doesn't put components on PYTHONPATH. If
# document-store's IdentityService drifts from this module, the suite at
# components/document-store/tests/test_identity.py fails.


# ---------------------------------------------------------------------------
# Published-copy parity
# ---------------------------------------------------------------------------
#
# The algorithm is published in more than one doc. Only data-models.md was
# anchored (by the digest above); the other copies drifted unnoticed — one of
# them described a retired pipe-delimited `field=value|field=value` form, which
# an external reimplementer would have used to compute wrong dedup hashes.
# These tests keep every published copy in step with the canonical-JSON
# algorithm this module actually executes.

from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]

# Every doc that publishes the identity-hash algorithm. Add new ones here.
PUBLISHED_COPIES = [
    REPO_ROOT / "docs" / "data-models.md",
    REPO_ROOT / "docs" / "uniqueness-and-identity.md",
    REPO_ROOT / "docs" / "glossary.md",
]

# The retired form: a delimiter-joined "field=value|field=value" string. Any
# doc still describing it is teaching an algorithm the platform does not run.
RETIRED_FORMS = ["field=value|", "field1=value1|", 'f"{field}={']


class TestPublishedCopiesStayInStep:
    """Each published copy must describe the canonical-JSON algorithm."""

    def test_every_published_copy_exists(self):
        for path in PUBLISHED_COPIES:
            assert path.is_file(), f"published identity-hash copy missing: {path}"

    def test_no_copy_describes_the_retired_delimited_form(self):
        for path in PUBLISHED_COPIES:
            text = path.read_text(encoding="utf-8")
            for retired in RETIRED_FORMS:
                assert retired not in text, (
                    f"{path.name} still documents the retired delimited identity-hash "
                    f"form ({retired!r}). The algorithm is canonical JSON — see the "
                    f"worked example in docs/data-models.md."
                )

    def test_every_published_copy_names_the_canonical_json_algorithm(self):
        for path in PUBLISHED_COPIES:
            text = path.read_text(encoding="utf-8")
            assert "sort_keys" in text, (
                f"{path.name} publishes the identity hash without naming the "
                f"canonical-JSON serialisation (sort_keys). Keep every copy in "
                f"step with wip_auth.document_identity.compute_hash."
            )

    def test_every_published_copy_names_the_resolved_referent_feed(self):
        # The feed rule is half the contract: a term/reference identity field
        # contributes its resolved referent's canonical ID, not the submitted
        # spelling. A copy describing only the serialisation teaches an
        # external reimplementer to hash spellings — the exact fork the rule
        # exists to prevent.
        for path in PUBLISHED_COPIES:
            text = path.read_text(encoding="utf-8")
            assert "resolved" in text.lower() and "spelling" in text.lower(), (
                f"{path.name} publishes the identity hash without the "
                f"resolved-referent feed rule (term/reference identity fields "
                f"hash resolved IDs, never submitted spellings). Keep every "
                f"copy in step with wip_auth.document_identity."
            )

    def test_doc_worked_feed_example_matches_published_digest(self):
        # data-models.md's worked example for the feed rule.
        text = (REPO_ROOT / "docs" / "data-models.md").read_text(encoding="utf-8")
        assert RESOLVED_FEED_DIGEST in text, (
            "docs/data-models.md no longer carries the resolved-referent "
            "worked-example digest. Update the doc and this test together."
        )
        assert compute_hash({"app": RESOLVED_FEED_TERM_ID}) == RESOLVED_FEED_DIGEST
