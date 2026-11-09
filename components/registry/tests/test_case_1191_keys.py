"""Runtime API keys: a reservation is written only by its owner and never
leaves half a key behind; a kept reservation does not lock the key's scope;
one request cannot claim one ID prefix twice; a disabled key-file key is
still listed; the create response says where the key came from.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from registry.models.api_key import StoredAPIKey
from registry.models.grant import NamespaceGrant
from registry.models.reservation import RuntimeReservation
from registry.services import key_import, key_shadowing, reservation_store
from registry.services.reservation_store import ReservationRefused
from registry.services.reservations import ReservationSettings
from tests.test_case_1033_key_import import _app_entry, _key_file, _provider, _res_file
from wip_auth.models import APIKeyRecord
from wip_auth.providers.api_key import api_key_prehash, hash_api_key

KEYS = "/api/registry/api-keys"
NS = "/api/registry/namespaces"


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def provider():
    """The Registry's own key provider, its config keys put back after."""
    provider = _provider()
    names = provider.config_key_names()
    original = [k for k in provider.iter_keys() if k.name in names]
    key_shadowing.reset_for_tests()
    yield provider
    provider.reload_config_keys(original)
    key_shadowing.reset_for_tests()


def _id_config(prefix: str) -> dict:
    return {"id_config": {"documents": {"algorithm": "prefixed", "prefix": prefix, "pad": 6}}}


@pytest.mark.asyncio
async def test_a_reservation_save_never_takes_over_another_keys_reservation(client: AsyncClient, auth_headers):
    prefix = _p("res")
    await reservation_store.save_reservation(
        prefix, ReservationSettings(description="A's"), "key-a", "test",
    )
    with pytest.raises(ReservationRefused) as refused:
        await reservation_store.save_reservation(
            prefix, ReservationSettings(description="B's"), "key-b", "test",
        )
    assert refused.value.status_code == 409
    held = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    assert (held.app, held.settings.get("description")) == ("key-a", "A's")


@pytest.mark.asyncio
async def test_a_create_whose_reservation_is_refused_leaves_no_key(
    client: AsyncClient, auth_headers, monkeypatch, tmp_path,
):
    """The file takes the prefix after the request's check passed: the
    create is refused, and no key, grant or provider record is left."""
    prefix, name = _p("res"), _p("key")
    _res_file(monkeypatch, tmp_path, {"prefix": prefix, "app": "cooking"})
    await key_import.ensure_current()
    with patch("registry.api.api_keys._check_reservation", AsyncMock()):
        r = await client.post(KEYS, headers=auth_headers, json={
            "name": name, "namespaces": [prefix], "reserve": [{"prefix": prefix, "settings": {}}],
        })
    assert r.status_code == 409, r.text
    assert await StoredAPIKey.find_one(StoredAPIKey.name == name) is None
    assert await NamespaceGrant.find({"subject": name}).to_list() == []


@pytest.mark.asyncio
async def test_a_widen_whose_reservation_is_refused_changes_nothing(
    client: AsyncClient, auth_headers, monkeypatch, tmp_path,
):
    prefix, name = _p("res"), _p("key")
    r = await client.post(KEYS, headers=auth_headers, json={"name": name, "namespaces": [_p("ns")]})
    assert r.status_code == 201, r.text
    _res_file(monkeypatch, tmp_path, {"prefix": prefix, "app": "cooking"})
    await key_import.ensure_current()
    with patch("registry.api.api_keys._check_reservation", AsyncMock()):
        r = await client.put(f"{KEYS}/{name}/namespaces/{prefix}", headers=auth_headers, json={"reserve": {}})
    assert r.status_code == 409, r.text
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    assert prefix not in doc.namespaces
    assert await NamespaceGrant.find({"subject": name, "namespace": prefix}).to_list() == []


@pytest.mark.asyncio
async def test_a_kept_reservation_does_not_lock_the_keys_scope(client: AsyncClient, auth_headers):
    prefix, other, name = _p("shop"), _p("other"), _p("key")
    r = await client.post(KEYS, headers=auth_headers, json={
        "name": name, "namespaces": [prefix], "reserve": [{"prefix": prefix, "settings": {}}],
    })
    assert r.status_code == 201, r.text
    assert (await client.put(f"{NS}/{prefix}", headers=auth_headers, json={})).status_code == 200
    r = await client.delete(f"{KEYS}/{name}/namespaces/{prefix}", headers=auth_headers)
    assert r.json()["reservation"] == "kept", r.text

    r = await client.patch(f"{KEYS}/{name}", headers=auth_headers, json={"namespaces": [other]})
    assert r.status_code == 200, r.text
    assert r.json()["namespaces"] == [other]


@pytest.mark.asyncio
async def test_dropping_a_reserved_prefix_still_in_scope_is_refused(client: AsyncClient, auth_headers):
    prefix, name = _p("shop"), _p("key")
    await client.post(KEYS, headers=auth_headers, json={
        "name": name, "namespaces": [prefix], "reserve": [{"prefix": prefix, "settings": {}}],
    })
    r = await client.patch(f"{KEYS}/{name}", headers=auth_headers, json={"namespaces": [_p("x")]})
    assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_one_request_cannot_claim_one_id_prefix_twice(client: AsyncClient, auth_headers):
    a, b, name = _p("a"), _p("b"), _p("key")
    id_prefix = f"P{uuid.uuid4().hex[:4].upper()}-"
    r = await client.post(KEYS, headers=auth_headers, json={
        "name": name, "namespaces": [a, b],
        "reserve": [{"prefix": a, "settings": _id_config(id_prefix)},
                    {"prefix": b, "settings": _id_config(id_prefix)}],
    })
    assert r.status_code == 409, r.text
    assert id_prefix in r.json()["detail"]
    assert await StoredAPIKey.find_one(StoredAPIKey.name == name) is None
    assert await RuntimeReservation.find({"prefix": {"$in": [a, b]}}).to_list() == []


@pytest.mark.asyncio
async def test_a_disabled_key_file_key_is_listed_as_a_config_key(
    client: AsyncClient, auth_headers, monkeypatch, tmp_path, provider,
):
    name = _p("app-k")
    plaintext = "pt-" + name
    entry = {**_app_entry(name, plaintext, ["default"], None), "enabled": False}
    _key_file(monkeypatch, tmp_path, entry)
    record = APIKeyRecord(name=name, key_hash=hash_api_key(plaintext, provider.hash_salt),
                          namespaces=["default"], enabled=False)
    record._plaintext_prehash = api_key_prehash(plaintext, provider.hash_salt)
    names = provider.config_key_names()
    provider.reload_config_keys([*(k for k in provider.iter_keys() if k.name in names), record])
    try:
        await key_import.run_pass(provider)
        listed = [k for k in (await client.get(f"{KEYS}?page_size=1000", headers=auth_headers)).json()["items"]
                  if k["name"] == name]
        assert [(k["source"], k["enabled"]) for k in listed] == [("config", False)]
        one = (await client.get(f"{KEYS}/{name}", headers=auth_headers)).json()
        assert (one["source"], one["enabled"]) == ("config", False)
    finally:
        provider.reload_config_keys([k for k in provider.iter_keys() if k.name in names])


@pytest.mark.asyncio
async def test_the_create_response_carries_the_stored_origin(client: AsyncClient, auth_headers):
    name = _p("key")
    created = (await client.post(KEYS, headers=auth_headers, json={"name": name})).json()
    fetched = (await client.get(f"{KEYS}/{name}", headers=auth_headers)).json()
    assert created["origin"] == fetched["origin"] == "console"
