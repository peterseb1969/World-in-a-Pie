"""A grant answer fetched before an invalidation does not land after it.

Provenance: CASE-1269 (2b8644ae R2). A check that was waiting on the Registry
when a grant change dropped the cache used to write its (now stale) answer
into the cache afterwards, undoing the change for the rest of the 30 s TTL.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from wip_auth import permissions
from wip_auth.models import UserIdentity
from wip_auth.permissions import (
    clear_permission_cache,
    resolve_accessible_namespaces,
    resolve_permission,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_permission_cache()
    yield
    clear_permission_cache()


def _key() -> UserIdentity:
    return UserIdentity(
        user_id="apikey:probe", username="probe", auth_method="api_key",
        groups=[], raw_claims={"key_name": "probe", "namespaces": ["probe-ns"]},
    )


@pytest.mark.asyncio
async def test_a_permission_fetched_across_a_clear_is_answered_but_not_cached():
    answers = iter(["write", "none"])

    async def fetch(identity, namespace):
        answer = next(answers)
        if answer == "write":
            # The grant is revoked while this check waits on the Registry.
            clear_permission_cache()
        return answer

    with patch.object(permissions, "_fetch_permission_from_registry", side_effect=fetch):
        assert await resolve_permission(_key(), "probe-ns") == "write"
        # The next check asks again instead of reading the stale "write".
        assert await resolve_permission(_key(), "probe-ns") == "none"


@pytest.mark.asyncio
async def test_a_permission_fetched_without_a_clear_is_cached():
    calls = 0

    async def fetch(identity, namespace):
        nonlocal calls
        calls += 1
        return "read"

    with patch.object(permissions, "_fetch_permission_from_registry", side_effect=fetch):
        assert await resolve_permission(_key(), "probe-ns") == "read"
        assert await resolve_permission(_key(), "probe-ns") == "read"
    assert calls == 1


@pytest.mark.asyncio
async def test_accessible_namespaces_fetched_across_a_clear_are_not_cached():
    answers = iter([["probe-ns", "gone-ns"], ["probe-ns"]])

    async def fetch(identity):
        answer = next(answers)
        if "gone-ns" in answer:
            clear_permission_cache()
        return answer

    with patch.object(permissions, "_fetch_accessible_from_registry", side_effect=fetch):
        assert await resolve_accessible_namespaces(_key()) == ["probe-ns", "gone-ns"]
        assert await resolve_accessible_namespaces(_key()) == ["probe-ns"]
