"""Namespace reservations at the namespace upsert.

The install reserves an app's namespaces, with operator-approved settings,
in WIP_NAMESPACE_RESERVATIONS_FILE. A key that is not a platform admin may
then upsert such a namespace when it holds `admin` on it, sending no
settings or only the reserved ones; omitted settings on create come from
the reservation for every caller. A reservation never retunes an existing
namespace.
"""

import json
import uuid

import pytest
from httpx import AsyncClient

from registry.models.namespace import Namespace
from registry.services.reservations import RESERVATIONS_FILE_ENV

BASE = "/api/registry/namespaces"


@pytest.fixture
def app_key():
    """A spec-declared (config-file) key with admin on one namespace and no
    groups: the key an app holds. Yields (headers, prefix)."""
    from registry.api import api_keys as api_keys_module
    from wip_auth.models import APIKeyRecord
    from wip_auth.providers.api_key import hash_api_key

    prefix = f"res-{uuid.uuid4().hex[:8]}"
    plaintext = f"app-key-{uuid.uuid4().hex}"
    provider = api_keys_module._get_provider()
    before = [k for k in provider.iter_keys() if k.name in provider.config_key_names()]
    record = APIKeyRecord(
        name=f"app-{prefix}",
        key_hash=hash_api_key(plaintext),
        owner="system:test",
        namespaces=[prefix],
        grants={prefix: "admin"},
    )
    provider.reload_config_keys([*before, record])
    try:
        yield {"X-API-Key": plaintext}, prefix
    finally:
        provider.reload_config_keys(before)


def _reserve(monkeypatch, tmp_path, *reservations: dict) -> None:
    path = tmp_path / "namespace-reservations.json"
    path.write_text(json.dumps({"reservations": list(reservations)}))
    monkeypatch.setenv(RESERVATIONS_FILE_ENV, str(path))


RESERVED_SETTINGS = {
    "description": "Cooking app data",
    "isolation_mode": "strict",
    "allowed_external_refs": ["wip"],
    "deletion_mode": "full",
}


@pytest.mark.asyncio
async def test_app_key_creates_its_reserved_namespace_as_reserved(
    client: AsyncClient, app_key, monkeypatch, tmp_path
):
    headers, prefix = app_key
    _reserve(monkeypatch, tmp_path, {"prefix": prefix, "app": "cooking", "settings": RESERVED_SETTINGS})

    resp = await client.put(f"{BASE}/{prefix}", headers=headers, json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for field, value in RESERVED_SETTINGS.items():
        assert body[field] == value, field

    # Re-running the bootstrap is a no-op, with or without the reserved settings.
    again = await client.put(f"{BASE}/{prefix}", headers=headers, json={})
    assert again.status_code == 200, again.text
    same = await client.put(
        f"{BASE}/{prefix}", headers=headers, json={"isolation_mode": "strict"}
    )
    assert same.status_code == 200, same.text


@pytest.mark.asyncio
async def test_app_key_cannot_send_settings_other_than_reserved(
    client: AsyncClient, app_key, monkeypatch, tmp_path
):
    headers, prefix = app_key
    _reserve(monkeypatch, tmp_path, {"prefix": prefix, "app": "cooking", "settings": RESERVED_SETTINGS})

    resp = await client.put(
        f"{BASE}/{prefix}", headers=headers, json={"deletion_mode": "retain"}
    )
    assert resp.status_code == 403
    assert "deletion_mode" in resp.json()["detail"]
    assert await Namespace.find_one({"prefix": prefix}) is None

    # A setting the reservation leaves out is pinned to the default.
    resp = await client.put(
        f"{BASE}/{prefix}", headers=headers, json={"id_config": {"terms": {"algorithm": "uuid4"}}}
    )
    assert resp.status_code == 403
    assert "id_config" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_app_key_needs_a_reservation(
    client: AsyncClient, app_key, monkeypatch, tmp_path
):
    headers, prefix = app_key
    _reserve(monkeypatch, tmp_path, {"prefix": "someone-else", "app": "other"})
    resp = await client.put(f"{BASE}/{prefix}", headers=headers, json={})
    assert resp.status_code == 403
    assert await Namespace.find_one({"prefix": prefix}) is None


@pytest.mark.asyncio
async def test_app_key_needs_admin_on_the_reserved_namespace(
    client: AsyncClient, app_key, monkeypatch, tmp_path
):
    headers, _own = app_key
    other = f"res-{uuid.uuid4().hex[:8]}"
    _reserve(monkeypatch, tmp_path, {"prefix": other, "app": "other"})
    resp = await client.put(f"{BASE}/{other}", headers=headers, json={})
    assert resp.status_code == 403
    assert await Namespace.find_one({"prefix": other}) is None


@pytest.mark.asyncio
async def test_write_grant_is_not_enough(
    client: AsyncClient, monkeypatch, tmp_path
):
    from registry.api import api_keys as api_keys_module
    from wip_auth.models import APIKeyRecord
    from wip_auth.providers.api_key import hash_api_key

    prefix = f"res-{uuid.uuid4().hex[:8]}"
    plaintext = f"write-key-{uuid.uuid4().hex}"
    provider = api_keys_module._get_provider()
    before = [k for k in provider.iter_keys() if k.name in provider.config_key_names()]
    provider.reload_config_keys([
        *before,
        APIKeyRecord(
            name=f"w-{prefix}", key_hash=hash_api_key(plaintext), owner="system:test",
            namespaces=[prefix], grants={prefix: "write"},
        ),
    ])
    try:
        _reserve(monkeypatch, tmp_path, {"prefix": prefix, "app": "cooking"})
        resp = await client.put(f"{BASE}/{prefix}", headers={"X-API-Key": plaintext}, json={})
        assert resp.status_code == 403
    finally:
        provider.reload_config_keys(before)


@pytest.mark.asyncio
async def test_existing_namespace_is_never_retuned_by_an_app_key(
    client: AsyncClient, app_key, auth_headers, monkeypatch, tmp_path
):
    headers, prefix = app_key
    # The namespace exists at settings that differ from the (later) reservation.
    created = await client.put(
        f"{BASE}/{prefix}", headers=auth_headers, json={"isolation_mode": "open"}
    )
    assert created.status_code == 200, created.text
    _reserve(monkeypatch, tmp_path, {"prefix": prefix, "app": "cooking", "settings": RESERVED_SETTINGS})

    # Omitting settings is a no-op: the live namespace keeps its settings.
    resp = await client.put(f"{BASE}/{prefix}", headers=headers, json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["isolation_mode"] == "open"

    # Sending the reserved value would retune it: refused.
    resp = await client.put(
        f"{BASE}/{prefix}", headers=headers, json={"isolation_mode": "strict"}
    )
    assert resp.status_code == 403
    assert (await Namespace.find_one({"prefix": prefix})).isolation_mode == "open"


@pytest.mark.asyncio
async def test_admin_create_takes_omitted_settings_from_the_reservation(
    client: AsyncClient, auth_headers, monkeypatch, tmp_path
):
    prefix = f"res-{uuid.uuid4().hex[:8]}"
    _reserve(monkeypatch, tmp_path, {"prefix": prefix, "app": "cooking", "settings": RESERVED_SETTINGS})
    resp = await client.put(
        f"{BASE}/{prefix}", headers=auth_headers, json={"description": "operator override"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["description"] == "operator override"
    assert body["isolation_mode"] == "strict"
    assert body["deletion_mode"] == "full"


@pytest.mark.asyncio
async def test_admin_update_does_not_apply_a_reservation(
    client: AsyncClient, auth_headers, monkeypatch, tmp_path
):
    prefix = f"res-{uuid.uuid4().hex[:8]}"
    await client.put(f"{BASE}/{prefix}", headers=auth_headers, json={})
    _reserve(monkeypatch, tmp_path, {"prefix": prefix, "app": "cooking", "settings": RESERVED_SETTINGS})
    resp = await client.put(
        f"{BASE}/{prefix}", headers=auth_headers, json={"description": "x"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["isolation_mode"] == "open"


@pytest.mark.asyncio
async def test_unparseable_reservations_file_refuses_upserts(
    client: AsyncClient, auth_headers, monkeypatch, tmp_path
):
    path = tmp_path / "namespace-reservations.json"
    path.write_text("{not json")
    monkeypatch.setenv(RESERVATIONS_FILE_ENV, str(path))
    resp = await client.put(f"{BASE}/res-broken", headers=auth_headers, json={})
    assert resp.status_code == 503
    assert await Namespace.find_one({"prefix": "res-broken"}) is None


@pytest.mark.asyncio
async def test_missing_reservations_file_means_no_reservations(
    client: AsyncClient, app_key, auth_headers, monkeypatch, tmp_path
):
    headers, prefix = app_key
    monkeypatch.setenv(RESERVATIONS_FILE_ENV, str(tmp_path / "absent.json"))
    assert (await client.put(f"{BASE}/{prefix}", headers=headers, json={})).status_code == 403
    assert (await client.put(f"{BASE}/{prefix}", headers=auth_headers, json={})).status_code == 200


@pytest.mark.asyncio
async def test_invalid_reserved_id_config_is_a_409_naming_the_reservation(
    client: AsyncClient, app_key, monkeypatch, tmp_path
):
    headers, prefix = app_key
    _reserve(monkeypatch, tmp_path, {
        "prefix": prefix, "app": "cooking",
        "settings": {"id_config": {"terms": {"algorithm": "no-such-algorithm"}}},
    })
    resp = await client.put(f"{BASE}/{prefix}", headers=headers, json={})
    assert resp.status_code == 409
    assert "cooking" in resp.json()["detail"]
    assert await Namespace.find_one({"prefix": prefix}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reservations", [
    [{"prefix": "wip", "app": "x"}],
    [{"prefix": "dup", "app": "a"}, {"prefix": "dup", "app": "b"}],
])
async def test_reservations_file_rules(
    client: AsyncClient, auth_headers, monkeypatch, tmp_path, reservations
):
    _reserve(monkeypatch, tmp_path, *reservations)
    resp = await client.put(f"{BASE}/res-any", headers=auth_headers, json={})
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_description_is_free_on_create_and_ignored_after(
    client: AsyncClient, app_key, monkeypatch, tmp_path
):
    """The description governs nothing, so it never refuses an app's upsert:
    a create stores the one sent, an existing namespace keeps its own."""
    headers, prefix = app_key
    _reserve(monkeypatch, tmp_path, {"prefix": prefix, "app": "kb", "settings": RESERVED_SETTINGS})

    created = await client.put(
        f"{BASE}/{prefix}", headers=headers, json={"description": "my corpus"}
    )
    assert created.status_code == 200, created.text
    assert created.json()["description"] == "my corpus"
    assert created.json()["isolation_mode"] == "strict"  # the rest as reserved

    again = await client.put(
        f"{BASE}/{prefix}", headers=headers, json={"description": "renamed"}
    )
    assert again.status_code == 200, again.text
    assert again.json()["description"] == "my corpus"
    assert (await Namespace.find_one({"prefix": prefix})).description == "my corpus"

    # Every other setting still binds.
    refused = await client.put(
        f"{BASE}/{prefix}", headers=headers,
        json={"description": "x", "deletion_mode": "retain"},
    )
    assert refused.status_code == 403
