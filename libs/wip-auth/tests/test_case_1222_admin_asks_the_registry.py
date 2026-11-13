"""An administrator's permission on a namespace is the Registry's answer.

Provenance: CASE-1222. A service answered wip-admins "admin" itself, so a
namespace the Registry had locked for deletion stayed open to them in every
store. It now asks the Registry like for any caller, and drops its cached
answers when the Registry says access changed (it does when it takes the
lock). Which namespaces an administrator may list is the Registry's answer
too (CASE-1226 tests that).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from wip_auth.cache_events import access_changed
from wip_auth.models import UserIdentity
from wip_auth.permissions import (
    check_namespace_permission,
    clear_permission_cache,
    resolve_permission,
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


ADMIN = UserIdentity(
    user_id="apikey:legacy", username="legacy", auth_method="api_key", groups=["wip-admins"],
)


async def test_an_administrator_is_asked_of_the_registry(httpx_mock):
    httpx_mock.add_response(json={"permission": "admin"})
    assert await resolve_permission(ADMIN, "open") == "admin"
    request = httpx_mock.get_requests()[0]
    assert request.url.path == "/api/registry/my/check-permission"
    assert request.headers["X-User-Groups"] == '["wip-admins"]'


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



@pytest.mark.parametrize(("legacy", "api_key", "sent"), [
    ("legacy-key", "api-key", "legacy-key"),
    # Blanked to switch the legacy key record off: the service still
    # presents API_KEY, not an empty key the Registry answers 401.
    ("", "api-key", "api-key"),
    (None, "api-key", "api-key"),
])
async def test_the_service_presents_a_non_empty_key(httpx_mock, monkeypatch, legacy, api_key, sent):
    if legacy is None:
        monkeypatch.delenv("WIP_AUTH_LEGACY_API_KEY", raising=False)
    else:
        monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", legacy)
    monkeypatch.setenv("API_KEY", api_key)
    httpx_mock.add_response(json={"permission": "admin"})
    assert await resolve_permission(ADMIN, "open") == "admin"
    assert httpx_mock.get_requests()[0].headers["X-API-Key"] == sent
