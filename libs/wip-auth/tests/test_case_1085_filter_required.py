"""A namespace filter asked for write or admin keeps only namespaces that meet it.

Provenance: CASE-1085. Without an explicit namespace, resolve_namespace_filter
returned every namespace the caller could see, whatever `required` was, so
POST /documents/migrate (which relies on it for its write check) let a
read-only single-namespace key write by omitting the namespace. The same call
naming the namespace got 403.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

import wip_auth.permissions as permissions
from wip_auth.models import UserIdentity
from wip_auth.permissions import (
    PERMISSION_UNAVAILABLE,
    clear_permission_cache,
    resolve_namespace_filter,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WIP_AUTH_REGISTRY_URL", "http://registry.test")
    clear_permission_cache()
    with patch("wip_auth.permissions.get_auth_config") as cfg:
        cfg.return_value.admin_groups = ["wip-admins"]
        yield
    clear_permission_cache()


def _registry(monkeypatch, grants: dict[str, str]):
    """The Registry answers: accessible = the grant keys, permission per grant."""

    async def accessible(identity):
        return sorted(grants)

    async def permission(identity, namespace):
        return grants.get(namespace, "none")

    monkeypatch.setattr(permissions, "_fetch_accessible_from_registry", accessible)
    monkeypatch.setattr(permissions, "_fetch_permission_from_registry", permission)


def _key(namespaces: list[str], groups: list[str] | None = None) -> UserIdentity:
    return UserIdentity(
        user_id="apikey:probe", username="probe", auth_method="api_key",
        groups=groups or [], raw_claims={"key_name": "probe", "namespaces": namespaces},
    )


@pytest.mark.asyncio
async def test_read_only_key_without_namespace_is_refused_write(monkeypatch):
    _registry(monkeypatch, {"ct": "read"})
    with pytest.raises(HTTPException) as exc:
        await resolve_namespace_filter(_key(["ct"]), None, "write")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_read_only_key_without_namespace_is_refused_admin(monkeypatch):
    _registry(monkeypatch, {"ct": "write"})
    with pytest.raises(HTTPException) as exc:
        await resolve_namespace_filter(_key(["ct"]), None, "admin")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_write_key_without_namespace_gets_its_namespace(monkeypatch):
    _registry(monkeypatch, {"ct": "write"})
    nsf = await resolve_namespace_filter(_key(["ct"]), None, "write")
    assert nsf.namespaces == ["ct"]
    assert nsf.query == {"namespace": {"$in": ["ct"]}}


@pytest.mark.asyncio
async def test_mixed_grants_keep_only_the_writable(monkeypatch):
    _registry(monkeypatch, {"a": "read", "b": "write", "c": "admin"})
    nsf = await resolve_namespace_filter(_key(["a", "b", "c"]), None, "write")
    assert nsf.namespaces == ["b", "c"]


@pytest.mark.asyncio
async def test_read_is_unchanged(monkeypatch):
    _registry(monkeypatch, {"a": "read", "b": "write"})
    nsf = await resolve_namespace_filter(_key(["a", "b"]), None, "read")
    assert nsf.namespaces == ["a", "b"]


@pytest.mark.asyncio
async def test_superadmin_has_no_filter(monkeypatch):
    _registry(monkeypatch, {})
    nsf = await resolve_namespace_filter(_key([], groups=["wip-admins"]), None, "write")
    assert nsf.namespaces is None
    assert nsf.query == {}


@pytest.mark.asyncio
async def test_an_outage_on_one_namespace_is_503_not_a_smaller_list(monkeypatch):
    async def accessible(identity):
        return ["a", "b"]

    async def permission(identity, namespace):
        return "write" if namespace == "a" else PERMISSION_UNAVAILABLE

    monkeypatch.setattr(permissions, "_fetch_accessible_from_registry", accessible)
    monkeypatch.setattr(permissions, "_fetch_permission_from_registry", permission)
    with pytest.raises(HTTPException) as exc:
        await resolve_namespace_filter(_key(["a", "b"]), None, "write")
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_config_declared_read_scope_is_not_write(monkeypatch):
    """A config-file key's `namespaces` are read scope; only `grants` give write."""
    identity = UserIdentity(
        user_id="apikey:cfg", username="cfg", auth_method="api_key", groups=[],
        raw_claims={"key_name": "cfg", "namespaces": ["a", "b"], "grants": {"b": "write"}},
    )
    nsf = await resolve_namespace_filter(identity, None, "write")
    assert nsf.namespaces == ["b"]
