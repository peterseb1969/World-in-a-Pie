"""Resolution failures must say WHY, when the reason is an under-specified name.

A bare value resolves in the caller's own namespace only — it never falls back
to `allowed_external_refs`. So an entity can exist, be legitimately
referenceable, and still not be found under a bare name. The old message said
only "No terminology found for identifier: KB_TOPIC", which describes a missing
entity and sent two separate readers hunting for one that was there all along.

The hint is deliberately narrow: it fires only where a qualified form is both
available and absent.
"""

from __future__ import annotations

from wip_auth.resolve import EntityNotFoundError


class TestHintFires:
    def test_bare_value_gets_the_reason_and_a_worked_example(self):
        msg = str(EntityNotFoundError("KB_TOPIC", "terminology", "library"))
        assert "No terminology found for identifier: KB_TOPIC" in msg
        assert "searched namespace 'library'" in msg
        assert "does not fall back to allowed_external_refs" in msg
        assert "other-ns:KB_TOPIC" in msg

    def test_template_refs_get_it_too(self):
        msg = str(EntityNotFoundError("SOME_TEMPLATE", "template", "library"))
        assert "other-ns:SOME_TEMPLATE" in msg


class TestHintStaysQuiet:
    """Every case here would be actively misleading, not merely noisy."""

    def test_already_qualified_identifier(self):
        # It named a namespace and still missed: the entity really is absent.
        msg = str(EntityNotFoundError("kb:KB_TOPIC", "terminology", "library"))
        assert "fall back" not in msg
        assert "searched namespace 'library'" in msg

    def test_canonical_uuid(self):
        # "qualify it as other-ns:<uuid>" is nonsense — a UUID needs no namespace.
        uuid = "019f898b-46cd-7392-812e-49ed735224dd"
        msg = str(EntityNotFoundError(uuid, "terminology", "library"))
        assert "fall back" not in msg
        assert "other-ns:" not in msg

    def test_terms_are_excluded(self):
        # Terms cross namespaces as ns:terminology:value — three parts. The
        # two-part advice would be wrong, so terms get none of it.
        msg = str(EntityNotFoundError("approved", "term", "library"))
        assert "fall back" not in msg
        assert "other-ns:" not in msg

    def test_no_namespace_supplied_keeps_the_bare_message(self):
        # Transport / non-200 raises pass no namespace: those are
        # infrastructure failures, and identifier advice would misdirect.
        msg = str(EntityNotFoundError("KB_TOPIC", "terminology"))
        assert msg == "No terminology found for identifier: KB_TOPIC"


class TestBackwardsCompatible:
    def test_two_argument_construction_still_works(self):
        # Existing call sites and test doubles construct with two args.
        err = EntityNotFoundError("X", "template")
        assert err.identifier == "X"
        assert err.entity_type == "template"
        assert err.namespace is None
