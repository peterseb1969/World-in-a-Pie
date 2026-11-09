"""Permission lookups answer the question the request asked.

Provenance: CASE-1193 (wip-auth review, permissions R5/R6).

- A key's namespace scope is forwarded to the Registry only for an api-key
  identity. Another identity's ``namespaces`` claim (an OIDC token may carry
  any claim) is not a key scope, and a claim that is not a list of strings
  never crashes the check.
- A cached answer is reused only for the same question: the Registry's
  answer depends on the caller's groups, key scope, email and username as
  well as its user_id, so all of them are part of the cache key.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from wip_auth.models import UserIdentity
from wip_auth.permissions import (
    clear_permission_cache,
    resolve_accessible_namespaces,
    resolve_permission,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WIP_AUTH_REGISTRY_URL", "http://registry.test")
    clear_permission_cache()
    with patch("wip_auth.permissions.get_auth_config") as cfg:
        cfg.return_value.admin_groups = ["wip-admins"]
        yield
    clear_permission_cache()


def _user(groups: list[str], claims: dict | None = None) -> UserIdentity:
    return UserIdentity(
        user_id="alice-sub", username="alice", email="alice@example.org",
        auth_method="jwt", groups=groups, raw_claims=claims or {},
    )


def _key(namespaces: list[str] | None) -> UserIdentity:
    return UserIdentity(
        user_id="apikey:probe", username="probe", auth_method="api_key",
        groups=[], raw_claims={"key_name": "probe", "namespaces": namespaces},
    )


# -- R5: only an api key's scope is forwarded, and never crashes ---------------


@pytest.mark.parametrize("claim", [[1, 2], 5, {"ns": "x"}, "ns1,ns2"])
async def test_a_token_namespaces_claim_is_not_a_key_scope(httpx_mock, claim):
    httpx_mock.add_response(json={"permission": "read"})
    assert await resolve_permission(_user([], {"namespaces": claim}), "ns1") == "read"
    assert "X-Key-Namespaces" not in httpx_mock.get_requests()[0].headers


@pytest.mark.parametrize("claim", [[1, 2], 5, {"ns": "x"}, "ns1,ns2"])
async def test_a_token_namespaces_claim_is_not_a_key_scope_when_listing(httpx_mock, claim):
    httpx_mock.add_response(json={"namespaces": ["ns1"]})
    assert await resolve_accessible_namespaces(_user([], {"namespaces": claim})) == ["ns1"]
    assert "X-Key-Namespaces" not in httpx_mock.get_requests()[0].headers


async def test_an_api_key_scope_is_still_forwarded(httpx_mock):
    httpx_mock.add_response(json={"permission": "read"})
    httpx_mock.add_response(json={"namespaces": ["a"]})
    await resolve_permission(_key(["a", "b"]), "a")
    await resolve_accessible_namespaces(_key(["a", "b"]))
    for request in httpx_mock.get_requests():
        assert request.headers["X-Key-Namespaces"] == "a,b"


async def test_an_api_key_scope_that_is_not_a_list_of_strings_is_not_a_crash(httpx_mock):
    """A key record's namespaces are a validated list; a malformed claim is
    still answered (by the Registry, unscoped) rather than raising."""
    identity = UserIdentity(
        user_id="apikey:odd", username="odd", auth_method="api_key",
        groups=[], raw_claims={"namespaces": [1, 2]},
    )
    httpx_mock.add_response(json={"permission": "none"})
    assert await resolve_permission(identity, "a") == "none"


# -- R6: the cache answers only the question it was asked ----------------------


async def test_the_same_user_with_other_groups_is_asked_again(httpx_mock):
    httpx_mock.add_response(json={"permission": "write"})
    httpx_mock.add_response(json={"permission": "none"})
    assert await resolve_permission(_user(["clinical-editors"]), "ct") == "write"
    assert await resolve_permission(_user([]), "ct") == "none"


async def test_the_same_key_name_with_another_scope_is_asked_again(httpx_mock):
    httpx_mock.add_response(json={"permission": "write"})
    httpx_mock.add_response(json={"permission": "none"})
    assert await resolve_permission(_key(["ct"]), "ct") == "write"
    assert await resolve_permission(_key(["other"]), "ct") == "none"


async def test_the_same_question_is_still_answered_from_the_cache(httpx_mock):
    httpx_mock.add_response(json={"permission": "write"})  # one response only
    assert await resolve_permission(_user(["b", "a"]), "ct") == "write"
    assert await resolve_permission(_user(["a", "b"]), "ct") == "write"


async def test_accessible_namespaces_differ_by_groups(httpx_mock):
    httpx_mock.add_response(json={"namespaces": ["ct"]})
    httpx_mock.add_response(json={"namespaces": []})
    assert await resolve_accessible_namespaces(_user(["clinical-editors"])) == ["ct"]
    assert await resolve_accessible_namespaces(_user([])) == []


async def test_accessible_namespaces_differ_by_key_scope(httpx_mock):
    httpx_mock.add_response(json={"namespaces": ["a"]})
    httpx_mock.add_response(json={"namespaces": ["b"]})
    assert await resolve_accessible_namespaces(_key(["a"])) == ["a"]
    assert await resolve_accessible_namespaces(_key(["b"])) == ["b"]


async def test_accessible_namespaces_same_question_is_cached(httpx_mock):
    httpx_mock.add_response(json={"namespaces": ["ct"]})  # one response only
    assert await resolve_accessible_namespaces(_user(["x"])) == ["ct"]
    assert await resolve_accessible_namespaces(_user(["x"])) == ["ct"]
