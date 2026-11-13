"""A listing that names no namespace leaves out a namespace locked for
deletion, an administrator's included.

Provenance: CASE-1226. The accessible-namespaces answer was None ("no
filter") for an administrator, so its unscoped listings still read a
namespace while the Registry deleted it. The answer is now always the
Registry's list, which for an administrator is every namespace but a locked
one.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from wip_auth.cache_events import access_changed
from wip_auth.models import UserIdentity
from wip_auth.permissions import (
    clear_permission_cache,
    resolve_accessible_namespaces,
    resolve_namespace_filter,
)

pytestmark = pytest.mark.asyncio

ADMIN = UserIdentity(
    user_id="apikey:legacy", username="legacy", auth_method="api_key", groups=["wip-admins"],
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WIP_AUTH_REGISTRY_URL", "http://registry.test")
    # The key the permission lookup authenticates with: set here, so the
    # file passes alone and never depends on another test leaving it set.
    monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "test-service-key")
    clear_permission_cache()
    yield
    clear_permission_cache()


async def test_an_administrators_list_is_the_registrys(httpx_mock):
    httpx_mock.add_response(json={"namespaces": ["wip", "app"], "is_superadmin": True})
    assert await resolve_accessible_namespaces(ADMIN) == ["wip", "app"]
    request = httpx_mock.get_requests()[0]
    assert request.url.path == "/api/registry/my/accessible-namespaces"
    assert request.headers["X-User-Groups"] == '["wip-admins"]'


async def test_an_administrators_unscoped_filter_leaves_out_what_the_registry_left_out(httpx_mock):
    # "being-deleted" is locked: the Registry does not list it.
    httpx_mock.add_response(json={"namespaces": ["wip", "app"], "is_superadmin": True})
    nsf = await resolve_namespace_filter(ADMIN, None)
    assert nsf.query == {"namespace": {"$in": ["wip", "app"]}}


async def test_an_answer_without_a_list_is_no_answer(httpx_mock):
    # The flag alone is not a filter: a Registry that answers no list is
    # answered 503, never "every namespace".
    httpx_mock.add_response(json={"namespaces": None, "is_superadmin": True})
    with pytest.raises(HTTPException) as refused:
        await resolve_accessible_namespaces(ADMIN)
    assert refused.value.status_code == 503


async def test_a_list_cached_before_a_lock_is_dropped_when_access_changes(httpx_mock):
    httpx_mock.add_response(json={"namespaces": ["wip", "doomed"], "is_superadmin": True})
    httpx_mock.add_response(json={"namespaces": ["wip"], "is_superadmin": True})
    assert await resolve_accessible_namespaces(ADMIN) == ["wip", "doomed"]
    assert await resolve_accessible_namespaces(ADMIN) == ["wip", "doomed"]  # cached
    access_changed(keys=False)  # the Registry locked "doomed"
    assert await resolve_accessible_namespaces(ADMIN) == ["wip"]
