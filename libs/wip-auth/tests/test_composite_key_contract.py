"""Contract tests for wip_auth.composite_key.

Every composite-key hash already stored in a Registry was produced by this
algorithm. It is therefore not free to change: a drift would not raise, it
would silently stop matching every entry, claim and synonym ever written —
the uniqueness gate would quietly open. The digests below are computed from
the algorithm as it stood when it moved out of the Registry
(``registry/services/hash.py``), so this file fails if the bytes ever move.

The sibling contract lives in ``test_document_identity_contract.py``; the two
hashes are different things (see the module docstring) and neither should be
used where the other belongs.
"""

from wip_auth.composite_key import (
    compute_composite_key_hash,
    verify_composite_key_hash,
)


class TestAlgorithmIsFrozen:
    """The digest must not move — stored hashes depend on it.

    Both key shapes below are the real ones the services register:
    def-store's terminology key and its term key.
    """

    def test_empty_key_hashes_as_empty_json_object(self):
        # sha256(b"{}") — pinned because an empty composite key is a real
        # state (an entity opting out of dedup registers one), and the value
        # must not drift into, say, the digest of an empty string.
        assert compute_composite_key_hash({}) == (
            "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
        )

    def test_terminology_key_digest(self):
        key = {"ns": "kb", "value": "GENDER", "label": "Gender"}
        assert compute_composite_key_hash(key) == (
            "67e7d61fb7e523119a858825eb1b634e5f8d27cd0d3f3448bdd792e9847c1565"
        )

    def test_term_key_digest(self):
        key = {"ns": "kb", "terminology_id": "019f-abc", "value": "M"}
        assert compute_composite_key_hash(key) == (
            "07af57d99771840b0f045944d852197b9a76cab5436739deb750b0761ab34cb2"
        )


class TestCanonicalisation:
    """Properties every stored hash relies on."""

    def test_key_order_is_irrelevant(self):
        assert compute_composite_key_hash({"b": 1, "a": 2}) == (
            compute_composite_key_hash({"a": 2, "b": 1})
        )

    def test_nested_key_order_is_irrelevant(self):
        assert compute_composite_key_hash({"x": {"b": 1, "a": 2}}) == (
            compute_composite_key_hash({"x": {"a": 2, "b": 1}})
        )

    def test_list_order_is_significant(self):
        # Lists are ordered data, not sets. The recursive sort deliberately
        # leaves element order alone.
        assert compute_composite_key_hash({"t": [1, 2]}) != (
            compute_composite_key_hash({"t": [2, 1]})
        )

    def test_values_are_case_sensitive(self):
        # A normalizing (case-folding) variant was deleted in CASE-568 for
        # disagreeing with this one. Case sensitivity is the contract.
        assert compute_composite_key_hash({"v": "GENDER"}) != (
            compute_composite_key_hash({"v": "gender"})
        )

    def test_type_is_significant(self):
        assert compute_composite_key_hash({"v": 1}) != (
            compute_composite_key_hash({"v": "1"})
        )

    def test_digest_is_hex_sha256(self):
        digest = compute_composite_key_hash({"ns": "kb", "value": "X"})
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)


class TestVerify:
    def test_verify_accepts_the_matching_hash(self):
        key = {"ns": "kb", "value": "GENDER"}
        assert verify_composite_key_hash(key, compute_composite_key_hash(key))

    def test_verify_rejects_a_different_key(self):
        assert not verify_composite_key_hash(
            {"ns": "kb", "value": "GENDER"},
            compute_composite_key_hash({"ns": "kb", "value": "COUNTRY"}),
        )
