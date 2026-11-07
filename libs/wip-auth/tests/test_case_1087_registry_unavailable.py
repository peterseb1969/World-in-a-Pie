"""A Registry that does not answer is never read as a Registry that said no.

Provenance: CASE-1087. Resolution, candidate lookup, the bulk write door,
permission checks and the deletion-mode lookup all turned "the Registry did
not answer" (unreachable, timeout, an error status) into a definite answer:
not found, no candidates, items passed on unverified, permission "none"
(cached for 30 s), deletion_mode "retain". Each now raises or reports
RegistryUnavailableError / PERMISSION_UNAVAILABLE, which every service turns
into 503, and an ambiguous identifier at a route is 409 with its candidates.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from wip_auth import RegistryUnavailableError, registry_unavailable_handler
from wip_auth.fastapi_helpers import (
    resolve_bulk_ids,
    resolve_or_404,
    resolve_term_by_fields_or_404,
)
from wip_auth.models import UserIdentity
from wip_auth.permissions import (
    PERMISSION_UNAVAILABLE,
    check_namespace_permission,
    clear_permission_cache,
    resolve_accessible_namespaces,
    resolve_permission,
)
from wip_auth.registry_client import RegistryClientBase
from wip_auth.resolve import (
    EntityNotFoundError,
    clear_resolution_cache,
    resolve_entity_id,
    resolve_entity_ids,
    terminology_coordinates_by_id,
)
from wip_auth.term_resolution import resolve_term_values

RESOLVE_URL = "http://localhost:8001/api/registry/entries/resolve"
CANDIDATES_URL = "http://localhost:8001/api/registry/entries/lookup/candidates"
BY_ID_URL = "http://localhost:8001/api/registry/entries/lookup/by-id"
BASE = "http://registry.test:8001"
U1 = "01903f5a-0000-7000-8000-000000000001"
U2 = "01903f5a-0000-7000-8000-000000000002"

NOT_FOUND = {"results": [{"status": "not_found"}]}


@pytest.fixture(autouse=True)
def _clean():
    clear_resolution_cache()
    clear_permission_cache()
    yield
    clear_resolution_cache()
    clear_permission_cache()


# -- resolution ------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 503, 401, 403, 422])
async def test_an_error_status_on_resolve_is_unavailable_not_a_miss(httpx_mock, status):
    httpx_mock.add_response(url=RESOLVE_URL, status_code=status)
    with pytest.raises(RegistryUnavailableError) as exc:
        await resolve_entity_id("BOOK", "template", "wip")
    assert not isinstance(exc.value, EntityNotFoundError)


@pytest.mark.asyncio
async def test_a_timeout_on_resolve_is_unavailable(httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=RESOLVE_URL)
    with pytest.raises(RegistryUnavailableError):
        await resolve_entity_id("BOOK", "template", "wip")


@pytest.mark.asyncio
async def test_a_genuine_miss_is_still_not_found(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json=NOT_FOUND)
    httpx_mock.add_response(url=CANDIDATES_URL, json=NOT_FOUND)
    with pytest.raises(EntityNotFoundError):
        await resolve_entity_id("BOOK", "template", "wip")


@pytest.mark.asyncio
async def test_the_value_fallback_does_not_turn_an_outage_into_a_miss(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json=NOT_FOUND)
    httpx_mock.add_response(url=CANDIDATES_URL, status_code=500)
    with pytest.raises(RegistryUnavailableError):
        await resolve_entity_id("BOOK", "template", "wip")


@pytest.mark.asyncio
async def test_batch_resolve_error_status_is_unavailable(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, status_code=500)
    with pytest.raises(RegistryUnavailableError):
        await resolve_entity_ids(["A", "B"], "template", "wip", strict=False)


@pytest.mark.asyncio
async def test_batch_value_fallback_outage_is_unavailable(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json=NOT_FOUND)
    httpx_mock.add_response(url=CANDIDATES_URL, status_code=502)
    with pytest.raises(RegistryUnavailableError):
        await resolve_entity_ids(["A"], "template", "wip", strict=False)


@pytest.mark.asyncio
async def test_terminology_lookup_outage_is_unavailable_not_none(httpx_mock):
    httpx_mock.add_response(url=BY_ID_URL, status_code=500)
    with pytest.raises(RegistryUnavailableError):
        await terminology_coordinates_by_id(U1)


@pytest.mark.asyncio
async def test_term_values_outage_is_not_terminology_not_found(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, status_code=503)
    with pytest.raises(RegistryUnavailableError):
        await resolve_term_values(
            [{"terminology_ref": "GENDER", "value": "M"}], "wip",
            base_url="http://def-store.test", api_key="k", timeout=5.0,
        )


# -- the bulk write door -----------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_door_refuses_on_an_error_status(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, status_code=500)
    items = [{"ref": "A"}]
    with pytest.raises(RegistryUnavailableError):
        await resolve_bulk_ids(items, "ref", "template", "wip", bypass_cache=True)
    assert items == [{"ref": "A"}]


@pytest.mark.asyncio
async def test_bulk_door_still_passes_a_genuine_miss_through(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json=NOT_FOUND)
    httpx_mock.add_response(url=CANDIDATES_URL, json=NOT_FOUND)
    items = [{"ref": "A"}]
    await resolve_bulk_ids(items, "ref", "template", "wip", bypass_cache=True)
    assert items == [{"ref": "A"}]


# -- route answers: 503 for an outage, 409 for ambiguity ------------------------------


def _route_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(RegistryUnavailableError, registry_unavailable_handler)

    @app.get("/things/{ref}")
    async def get_thing(ref: str):
        return {"id": await resolve_or_404(ref, "template", "wip")}

    return app


def test_resolve_or_404_outage_is_503_through_the_handler(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=RESOLVE_URL)
    response = TestClient(_route_app()).get("/things/BOOK")
    assert response.status_code == 503
    assert response.json()["error_code"] == "registry_unavailable"


def test_resolve_or_404_genuine_miss_stays_404(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json=NOT_FOUND)
    httpx_mock.add_response(url=CANDIDATES_URL, json=NOT_FOUND)
    assert TestClient(_route_app()).get("/things/BOOK").status_code == 404


def _two_candidates() -> dict:
    return {"results": [{
        "status": "found", "total": 2,
        "candidates": [
            {"entry_id": U1, "matched_composite_key": {"@template": "A", "title": "X"}},
            {"entry_id": U2, "matched_composite_key": {"@template": "B", "title": "X"}},
        ],
    }]}


@pytest.mark.asyncio
async def test_resolve_or_404_ambiguity_is_409_with_candidates(httpx_mock):
    httpx_mock.add_response(url=RESOLVE_URL, json=NOT_FOUND)
    httpx_mock.add_response(url=CANDIDATES_URL, json=_two_candidates())
    with pytest.raises(HTTPException) as exc:
        await resolve_or_404("X", "document", "wip")
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert detail["error"] == "ambiguous"
    assert detail["total"] == 2
    assert [c["entry_id"] for c in detail["candidates"]] == [U1, U2]


@pytest.mark.asyncio
async def test_term_fields_or_404_outage_is_not_404(httpx_mock):
    httpx_mock.add_response(status_code=500)
    with pytest.raises(RegistryUnavailableError):
        await resolve_term_by_fields_or_404("M", "wip:GENDER", "wip")


def test_setup_auth_registers_the_503_handler(monkeypatch):
    from wip_auth import setup_auth
    from wip_auth.config import AuthConfig

    app = FastAPI()
    setup_auth(app, config=AuthConfig(mode="none"))
    assert app.exception_handlers.get(RegistryUnavailableError) is registry_unavailable_handler


# -- candidates and deletion mode (the shared client) ------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 401])
async def test_resolve_candidates_error_status_is_unavailable(httpx_mock, status):
    httpx_mock.add_response(url=f"{BASE}/api/registry/entries/lookup/candidates", status_code=status)
    client = RegistryClientBase(base_url=BASE, api_key="k")
    with pytest.raises(RegistryUnavailableError):
        await client.resolve_candidates("wip", "documents", "X")


@pytest.mark.asyncio
async def test_resolve_candidates_connect_error_is_unavailable_not_raw_httpx(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    client = RegistryClientBase(base_url=BASE, api_key="k")
    with pytest.raises(RegistryUnavailableError):
        await client.resolve_candidates("wip", "documents", "X")


@pytest.mark.asyncio
async def test_resolve_candidates_genuine_miss_is_empty(httpx_mock):
    httpx_mock.add_response(url=f"{BASE}/api/registry/entries/lookup/candidates", json=NOT_FOUND)
    client = RegistryClientBase(base_url=BASE, api_key="k")
    assert await client.resolve_candidates("wip", "documents", "X") == ([], 0)


NS_URL = f"{BASE}/api/registry/namespaces/books"


@pytest.mark.asyncio
async def test_deletion_mode_error_status_is_unavailable_not_retain(httpx_mock):
    httpx_mock.add_response(url=NS_URL, status_code=500)
    client = RegistryClientBase(base_url=BASE, api_key="k")
    with pytest.raises(RegistryUnavailableError):
        await client.get_namespace_deletion_mode("books")


@pytest.mark.asyncio
async def test_deletion_mode_unreachable_is_unavailable(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=NS_URL)
    client = RegistryClientBase(base_url=BASE, api_key="k")
    with pytest.raises(RegistryUnavailableError):
        await client.get_namespace_deletion_mode("books")


@pytest.mark.asyncio
async def test_deletion_mode_answers(httpx_mock):
    httpx_mock.add_response(url=NS_URL, json={"prefix": "books", "deletion_mode": "full"})
    httpx_mock.add_response(url=NS_URL, status_code=404)
    client = RegistryClientBase(base_url=BASE, api_key="k")
    assert await client.get_namespace_deletion_mode("books") == "full"
    assert await client.get_namespace_deletion_mode("books") == "retain"


# -- permissions ---------------------------------------------------------------------


@pytest.fixture
def _perm_env(monkeypatch):
    monkeypatch.setenv("WIP_AUTH_REGISTRY_URL", "http://registry.test")
    with patch("wip_auth.permissions.get_auth_config") as cfg:
        cfg.return_value.admin_groups = ["wip-admins"]
        yield


def _key() -> UserIdentity:
    return UserIdentity(
        user_id="apikey:probe", username="probe", auth_method="api_key",
        groups=[], raw_claims={"key_name": "probe", "namespaces": ["probe-ns"]},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_a_4xx_permission_answer_is_unavailable_and_uncached(httpx_mock, _perm_env, status):
    httpx_mock.add_response(status_code=status)
    httpx_mock.add_response(json={"permission": "write"})
    assert await resolve_permission(_key(), "probe-ns") == PERMISSION_UNAVAILABLE
    assert await resolve_permission(_key(), "probe-ns") == "write"


@pytest.mark.asyncio
async def test_a_4xx_permission_answer_is_503_not_404(httpx_mock, _perm_env):
    httpx_mock.add_response(status_code=401)
    with pytest.raises(HTTPException) as exc:
        await check_namespace_permission(_key(), "probe-ns", "read")
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_a_200_without_a_permission_is_unavailable(httpx_mock, _perm_env):
    httpx_mock.add_response(json={"namespace": "probe-ns"})
    assert await resolve_permission(_key(), "probe-ns") == PERMISSION_UNAVAILABLE


@pytest.mark.asyncio
async def test_a_4xx_accessible_namespaces_answer_is_503_not_empty(httpx_mock, _perm_env):
    httpx_mock.add_response(status_code=401)
    httpx_mock.add_response(json={"namespaces": ["probe-ns"]})
    with pytest.raises(HTTPException) as exc:
        await resolve_accessible_namespaces(_key())
    assert exc.value.status_code == 503
    assert await resolve_accessible_namespaces(_key()) == ["probe-ns"]  # nothing was cached
