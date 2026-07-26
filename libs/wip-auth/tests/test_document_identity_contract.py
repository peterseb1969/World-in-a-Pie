"""Contract tests for wip_auth.document_identity.

The whole point of CASE-402 is that the algorithm published in
``docs/data-models.md`` must match the algorithm executed by the platform.
The digest below is the worked example printed in that doc. If the
algorithm drifts, this test fails AND the doc must be re-published in the
same change. Doc and code stay co-versioned by construction.
"""

from wip_auth.document_identity import (
    compute_hash,
    compute_identity_hash,
    compute_normalized_hash,
    extract_identity_values,
    normalize_value,
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
