"""A user's groups reach the Registry as one JSON array, never comma-joined.

Provenance: CASE-1035 (reopen). A group name may contain a comma. Joined with
commas and split again by the Registry, a JWT group named "staff,wip-admins"
was read as wip-admins, and the Registry answered admin everywhere.
"""

from __future__ import annotations

import json

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
    # The key the permission lookup authenticates with: set here, so the
    # file passes alone and never depends on another test leaving it set.
    monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "test-service-key")
    clear_permission_cache()
    yield
    clear_permission_cache()


def _user(*groups: str) -> UserIdentity:
    return UserIdentity(
        user_id="sub-1", username="alice", email="alice@example.com",
        auth_method="jwt", groups=list(groups),
    )


async def test_a_comma_in_a_group_name_stays_inside_that_group(httpx_mock):
    httpx_mock.add_response(json={"permission": "none"})
    await resolve_permission(_user("staff,wip-admins", "readers"), "ns")
    sent = httpx_mock.get_requests()[0].headers["X-User-Groups"]
    # The names arrive whole (sent sorted: order does not change the answer).
    assert sorted(json.loads(sent)) == ["readers", "staff,wip-admins"]


async def test_a_name_outside_ascii_fits_in_the_header(httpx_mock):
    httpx_mock.add_response(json={"permission": "none"})
    await resolve_permission(_user("équipe"), "ns")
    sent = httpx_mock.get_requests()[0].headers["X-User-Groups"]
    assert sent.isascii()
    assert json.loads(sent) == ["équipe"]


async def test_the_accessible_namespaces_question_sends_the_same_array(httpx_mock):
    httpx_mock.add_response(json={"namespaces": [], "is_superadmin": False})
    await resolve_accessible_namespaces(_user("staff,wip-admins"))
    request = httpx_mock.get_requests()[0]
    assert request.url.path == "/api/registry/my/accessible-namespaces"
    assert json.loads(request.headers["X-User-Groups"]) == ["staff,wip-admins"]


async def test_no_groups_sends_no_header(httpx_mock):
    httpx_mock.add_response(json={"permission": "none"})
    await resolve_permission(_user(), "ns")
    assert "X-User-Groups" not in httpx_mock.get_requests()[0].headers
