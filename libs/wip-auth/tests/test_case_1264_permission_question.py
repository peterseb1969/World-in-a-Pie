"""A malformed groups claim is no groups, and the permission cache is keyed
on the question the Registry is asked.

Provenance: CASE-1264 (commit-review backfill, efcdf0f2 R5 and R7). A
signed token whose groups claim was not a list of names (a number, an
object) made every request of that user a 500. The permission cache rebuilt
the identity half of its key by hand, so a factor added to the question
could be left out of the key and two callers would share an answer.
"""
def test_a_malformed_groups_claim_is_no_groups_not_a_crash():
    from wip_auth.providers.oidc import _claimed_groups

    assert _claimed_groups("staff") == ["staff"]
    assert _claimed_groups(["a", 5, None, "b"]) == ["a", "b"]
    assert _claimed_groups(5) == []
    assert _claimed_groups({"a": 1}) == []
    assert _claimed_groups(None) == []


def test_the_permission_cache_key_is_the_question_sent(monkeypatch):
    from wip_auth import UserIdentity, permissions

    identity = UserIdentity(user_id="u", username="u", groups=["g"], auth_method="api_key")
    before = permissions._cache_key(identity, "ns")
    real = permissions._registry_question

    def with_extra_header(identity, namespace=None):
        params, headers = real(identity, namespace)
        return params, {**headers, "X-Something-New": "1"}

    # A factor added to the question reaches the key without touching it.
    monkeypatch.setattr(permissions, "_registry_question", with_extra_header)
    assert permissions._cache_key(identity, "ns") != before
