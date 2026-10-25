"""A Registry outage is not a "no access" answer.

Provenance: CASE-862 S7 — a permission lookup that failed because the Registry
was unreachable returned "none", was cached for 30 s, and surfaced as 404
"Namespace not found" (or, on list endpoints, 403 / empty results) — and kept
doing so for the cache lifetime after the Registry was back. The lookup now
fails closed with 503 and caches only definitive answers.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException

from wip_auth.models import UserIdentity
from wip_auth.permissions import (
    PERMISSION_UNAVAILABLE,
    check_namespace_permission,
    clear_permission_cache,
    permission_sufficient,
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


def _key() -> UserIdentity:
    return UserIdentity(
        user_id="apikey:probe", username="probe", auth_method="api_key",
        groups=[], raw_claims={"key_name": "probe", "namespaces": ["probe-ns"]},
    )


# -- single-namespace check -----------------------------------------------------


@pytest.mark.asyncio
async def test_unreachable_registry_is_503_not_404(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    with pytest.raises(HTTPException) as exc:
        await check_namespace_permission(_key(), "probe-ns", "read")
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_a_5xx_registry_answer_is_unavailable(httpx_mock):
    httpx_mock.add_response(status_code=503)
    assert await resolve_permission(_key(), "probe-ns") == PERMISSION_UNAVAILABLE
    assert not permission_sufficient(PERMISSION_UNAVAILABLE, "read")  # still fails closed


@pytest.mark.asyncio
async def test_an_outage_is_not_cached(httpx_mock):
    """Access returns as soon as the Registry does — not 30 s later."""
    httpx_mock.add_response(status_code=503)
    httpx_mock.add_response(json={"permission": "write"})
    assert await resolve_permission(_key(), "probe-ns") == PERMISSION_UNAVAILABLE
    assert await resolve_permission(_key(), "probe-ns") == "write"


@pytest.mark.asyncio
async def test_a_definitive_answer_is_still_cached_and_still_404(httpx_mock):
    httpx_mock.add_response(json={"permission": "none"})  # one response only
    with pytest.raises(HTTPException) as exc:
        await check_namespace_permission(_key(), "probe-ns", "read")
    assert exc.value.status_code == 404
    assert await resolve_permission(_key(), "probe-ns") == "none"  # served from cache


# -- accessible namespaces --------------------------------------------------------


@pytest.mark.asyncio
async def test_accessible_namespaces_outage_is_503_and_not_cached(httpx_mock):
    httpx_mock.add_response(status_code=502)
    httpx_mock.add_response(json={"namespaces": ["probe-ns"]})
    with pytest.raises(HTTPException) as exc:
        await resolve_accessible_namespaces(_key())
    assert exc.value.status_code == 503
    assert await resolve_accessible_namespaces(_key()) == ["probe-ns"]


@pytest.mark.asyncio
async def test_accessible_namespaces_empty_answer_is_still_empty(httpx_mock):
    httpx_mock.add_response(json={"namespaces": []})
    assert await resolve_accessible_namespaces(_key()) == []
