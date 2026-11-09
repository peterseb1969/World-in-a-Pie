"""An administrator's permission on a namespace is the Registry's answer.

Provenance: CASE-1222. A service answered wip-admins "admin" itself, so a
namespace the Registry had locked for deletion stayed open to them in every
store. It now asks the Registry like for any caller, and drops its cached
answers when the Registry says access changed (it does when it takes the
lock). Listing which namespaces an administrator may read still needs no
lookup: that is every namespace, as the Registry answers too.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from wip_auth.cache_events import access_changed
from wip_auth.models import UserIdentity
from wip_auth.permissions import (
    check_namespace_permission,
    clear_permission_cache,
    resolve_accessible_namespaces,
    resolve_permission,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WIP_AUTH_REGISTRY_URL", "http://registry.test")
    clear_permission_cache()
    yield
    clear_permission_cache()


ADMIN = UserIdentity(
    user_id="apikey:legacy", username="legacy", auth_method="api_key", groups=["wip-admins"],
)


async def test_an_administrator_is_asked_of_the_registry(httpx_mock):
    httpx_mock.add_response(json={"permission": "admin"})
    assert await resolve_permission(ADMIN, "open") == "admin"
    request = httpx_mock.get_requests()[0]
    assert request.url.path == "/api/registry/my/check-permission"
    assert request.headers["X-User-Groups"] == "wip-admins"


async def test_a_locked_namespace_is_closed_to_an_administrator(httpx_mock):
    httpx_mock.add_response(json={"permission": "none"})
    with pytest.raises(HTTPException) as refused:
        await check_namespace_permission(ADMIN, "being-deleted", "read")
    assert refused.value.status_code == 404


async def test_an_answer_cached_before_the_lock_is_dropped_when_access_changes(httpx_mock):
    httpx_mock.add_response(json={"permission": "admin"})
    httpx_mock.add_response(json={"permission": "none"})
    assert await resolve_permission(ADMIN, "ns") == "admin"
    assert await resolve_permission(ADMIN, "ns") == "admin"  # cached
    access_changed(keys=False)  # the Registry locked "ns"
    assert await resolve_permission(ADMIN, "ns") == "none"


async def test_an_administrator_still_lists_every_namespace_without_a_lookup(httpx_mock):
    assert await resolve_accessible_namespaces(ADMIN) is None
    assert httpx_mock.get_requests() == []
