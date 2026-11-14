"""A key scoped to one namespace plus the platform's `wip`, held read-only,
implies that one namespace.

Provenance: CASE-512. Every app key the deployer derives carries `wip` (read)
beside the app's own namespace, so a rule that derived only from a one-entry
scope derived for no app key. The key's permission on `wip` is the Registry's
answer: a grant made through the API can raise it, and a key that may write
`wip` implies nothing, since omitting the namespace would then pick between
two namespaces it can write. An outage there is a 503, never a guess.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from wip_auth.fastapi_helpers import (
    _derive_namespace_from_identity,
    resolution_miss,
    resolve_bulk_ids,
)
from wip_auth.identity import set_current_identity
from wip_auth.models import UserIdentity
from wip_auth.permissions import clear_permission_cache

CHECK_PERMISSION = "http://registry.test/api/registry/my/check-permission"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WIP_AUTH_REGISTRY_URL", "http://registry.test")
    monkeypatch.setenv("WIP_AUTH_LEGACY_API_KEY", "test-service-key")
    clear_permission_cache()
    yield
    clear_permission_cache()
    set_current_identity(None)


def _key(namespaces: list[str], auth_method: str = "api_key") -> UserIdentity:
    return UserIdentity(
        user_id="apikey:app-aa",
        username="app-aa",
        groups=[],
        auth_method=auth_method,
        provider="api_key",
        raw_claims={"key_name": "app-aa", "namespaces": namespaces},
    )


def _wip_answer(httpx_mock, permission: str) -> None:
    httpx_mock.add_response(
        url=httpx.URL(CHECK_PERMISSION, params={
            "user_id": "apikey:app-aa", "auth_method": "api_key",
            "namespace": "wip", "username": "app-aa",
        }),
        json={"permission": permission},
    )


@pytest.mark.parametrize("scope", [["aa", "wip"], ["wip", "aa"]])
async def test_read_only_wip_derives_the_other_namespace(httpx_mock, scope):
    _wip_answer(httpx_mock, "read")
    set_current_identity(_key(scope))
    assert await _derive_namespace_from_identity() == "aa"
    (request,) = httpx_mock.get_requests()
    assert request.headers["X-Key-Namespaces"] == "aa,wip"


@pytest.mark.parametrize("permission", ["write", "admin"])
async def test_writable_wip_derives_nothing(httpx_mock, permission):
    _wip_answer(httpx_mock, permission)
    set_current_identity(_key(["aa", "wip"]))
    assert await _derive_namespace_from_identity() is None


async def test_no_permission_on_wip_derives_nothing(httpx_mock):
    # Not "wip held read-only": the rule derives past a read grant only.
    _wip_answer(httpx_mock, "none")
    set_current_identity(_key(["aa", "wip"]))
    assert await _derive_namespace_from_identity() is None


@pytest.mark.parametrize("status", [500, 401])
async def test_a_registry_that_cannot_answer_is_a_503(httpx_mock, status):
    httpx_mock.add_response(url=httpx.URL(CHECK_PERMISSION, params={
        "user_id": "apikey:app-aa", "auth_method": "api_key",
        "namespace": "wip", "username": "app-aa",
    }), status_code=status)
    set_current_identity(_key(["aa", "wip"]))
    with pytest.raises(HTTPException) as refused:
        await _derive_namespace_from_identity()
    assert refused.value.status_code == 503


async def test_an_unreachable_registry_is_a_503(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    set_current_identity(_key(["aa", "wip"]))
    with pytest.raises(HTTPException) as refused:
        await _derive_namespace_from_identity()
    assert refused.value.status_code == 503


async def test_the_answer_is_cached_like_every_permission_read(httpx_mock):
    _wip_answer(httpx_mock, "read")
    set_current_identity(_key(["aa", "wip"]))
    assert await _derive_namespace_from_identity() == "aa"
    assert await _derive_namespace_from_identity() == "aa"
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize("scope", [["aa"], ["wip"], ["aa", "bb"], ["aa", "bb", "wip"], []])
async def test_other_scopes_never_ask_the_registry(httpx_mock, scope):
    # httpx_mock has no response: any request would fail the test.
    set_current_identity(_key(scope))
    expected = scope[0] if len(scope) == 1 else None
    assert await _derive_namespace_from_identity() == expected


async def test_an_oidc_namespaces_claim_never_asks(httpx_mock):
    set_current_identity(_key(["aa", "wip"], auth_method="jwt"))
    assert await _derive_namespace_from_identity() is None


async def test_a_door_miss_names_the_derived_namespace(httpx_mock):
    """The 404 an unknown id gets in a door names the namespace the key
    implies, as for a one-namespace key."""
    _wip_answer(httpx_mock, "read")
    set_current_identity(_key(["aa", "wip"]))
    miss = await resolution_miss("NOPE", "template", None, "template_id")
    assert miss is not None
    assert miss.status_code == 404
    assert "in namespace 'aa'" in str(miss.detail)


async def test_bulk_items_without_a_namespace_resolve_in_the_derived_one(
    httpx_mock, monkeypatch
):
    _wip_answer(httpx_mock, "read")
    asked: dict[str, list[str]] = {}

    async def fake_resolve_ids(ids, entity_type, namespace, **_kw):
        asked.setdefault(namespace, []).extend(ids)
        return {i: f"{namespace}/{i}" for i in ids}

    from wip_auth import fastapi_helpers

    monkeypatch.setattr(fastapi_helpers, "resolve_entity_ids", fake_resolve_ids)
    set_current_identity(_key(["aa", "wip"]))
    items = [{"template_id": "PERSON"}]
    await resolve_bulk_ids(items, "template_id", "template", namespace=None)
    assert asked == {"aa": ["PERSON"]}
