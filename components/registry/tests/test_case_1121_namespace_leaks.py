"""Namespace routes reveal nothing to a caller without standing.

- A deletion journal (its storage keys, filters, counts and the entities of
  third namespaces it names) is read only by the platform admins, who alone
  can start a deletion; anyone else gets 404, as for a namespace with no
  journal.
- The namespace upsert decides whether a caller outside the admin groups
  may upsert before it reads the reservation's settings: such a caller
  cannot learn which app reserves a prefix, nor whether it is reserved, and
  a broken reservations file does not fail a platform admin's update (which
  never uses it).
- A deletion journal records the authenticated caller as the requester; the
  free-text ``deleted_by`` is kept beside it as a note.
"""

import json
import uuid

import pytest
from httpx import AsyncClient

from registry.services.reservations import RESERVATIONS_FILE_ENV

BASE = "/api/registry/namespaces"


@pytest.fixture
def app_key():
    """A config-file key with admin on one namespace and no groups (an app's
    key). Yields (headers, the namespace it administers)."""
    from registry.api import api_keys as api_keys_module
    from wip_auth.models import APIKeyRecord
    from wip_auth.providers.api_key import hash_api_key

    prefix = f"lk-{uuid.uuid4().hex[:8]}"
    plaintext = f"app-key-{uuid.uuid4().hex}"
    provider = api_keys_module._get_provider()
    before = [k for k in provider.iter_keys() if k.name in provider.config_key_names()]
    record = APIKeyRecord(
        name=f"app-{prefix}", key_hash=hash_api_key(plaintext), owner="system:test",
        namespaces=[prefix], grants={prefix: "admin"},
    )
    provider.reload_config_keys([*before, record])
    try:
        yield {"X-API-Key": plaintext}, prefix
    finally:
        provider.reload_config_keys(before)


def _reservations_file(monkeypatch, tmp_path, content: str) -> None:
    path = tmp_path / "namespace-reservations.json"
    path.write_text(content)
    monkeypatch.setenv(RESERVATIONS_FILE_ENV, str(path))


def _reserve(monkeypatch, tmp_path, *reservations: dict) -> None:
    _reservations_file(monkeypatch, tmp_path, json.dumps({"reservations": list(reservations)}))


_INVALID_ID_CONFIG = {"id_config": {"terms": {"algorithm": "prefixed", "prefix": ""}}}


async def _deleted_namespace(client: AsyncClient, headers: dict, **params) -> str:
    prefix = f"lkdel-{uuid.uuid4().hex[:8]}"
    r = await client.put(f"{BASE}/{prefix}", headers=headers, json={"deletion_mode": "full"})
    assert r.status_code == 200, r.text
    r = await client.delete(f"{BASE}/{prefix}", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return prefix


# ── deletion-status ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_deletion_journal_is_read_by_platform_admins_only(
    client: AsyncClient, auth_headers: dict, app_key,
):
    prefix = await _deleted_namespace(client, auth_headers)
    assert (await client.get(f"{BASE}/{prefix}/deletion-status", headers=auth_headers)).status_code == 200

    headers, _own = app_key
    r = await client.get(f"{BASE}/{prefix}/deletion-status", headers=headers)
    assert r.status_code == 404
    # The same answer as a namespace that never had a journal.
    assert r.json()["detail"] == f"No deletion journal found for namespace '{prefix}'"


@pytest.mark.asyncio
async def test_a_namespace_admin_does_not_read_its_journal_either(
    client: AsyncClient, auth_headers: dict, app_key,
):
    """Holding admin on a namespace does not make one a platform admin: only
    platform admins can delete a namespace, so only they read the journal."""
    headers, own = app_key
    r = await client.put(f"{BASE}/{own}", headers=auth_headers, json={"deletion_mode": "full"})
    assert r.status_code == 200, r.text
    assert (await client.delete(f"{BASE}/{own}", headers=auth_headers)).status_code == 200
    assert (await client.get(f"{BASE}/{own}/deletion-status", headers=headers)).status_code == 404


# ── the upsert's reservation lookup ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_caller_without_standing_does_not_learn_the_reserving_app(
    client: AsyncClient, app_key, monkeypatch, tmp_path,
):
    headers, _own = app_key
    target = f"lkres-{uuid.uuid4().hex[:8]}"
    _reserve(monkeypatch, tmp_path, {"prefix": target, "app": "secret-app", "settings": _INVALID_ID_CONFIG})

    r = await client.put(f"{BASE}/{target}", headers=headers, json={})
    assert r.status_code == 403, r.text
    assert "secret-app" not in r.text


@pytest.mark.asyncio
async def test_reserved_and_unreserved_prefixes_refuse_alike(
    client: AsyncClient, app_key, monkeypatch, tmp_path,
):
    headers, _own = app_key
    reserved, free = f"lkr-{uuid.uuid4().hex[:8]}", f"lkf-{uuid.uuid4().hex[:8]}"
    _reserve(monkeypatch, tmp_path, {"prefix": reserved, "app": "other"})

    a = await client.put(f"{BASE}/{reserved}", headers=headers, json={})
    b = await client.put(f"{BASE}/{free}", headers=headers, json={})
    assert a.status_code == b.status_code == 403
    assert a.json()["detail"].replace(reserved, "X") == b.json()["detail"].replace(free, "X")


@pytest.mark.asyncio
async def test_a_broken_reservations_file_refuses_a_caller_without_standing_as_403(
    client: AsyncClient, app_key, monkeypatch, tmp_path,
):
    headers, _own = app_key
    _reservations_file(monkeypatch, tmp_path, "{not json")
    r = await client.put(f"{BASE}/lkb-{uuid.uuid4().hex[:8]}", headers=headers, json={})
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_a_broken_reservations_file_does_not_fail_an_admin_update(
    client: AsyncClient, auth_headers: dict, monkeypatch, tmp_path,
):
    prefix = f"lku-{uuid.uuid4().hex[:8]}"
    assert (await client.put(f"{BASE}/{prefix}", headers=auth_headers, json={})).status_code == 200
    _reservations_file(monkeypatch, tmp_path, "{not json")

    r = await client.put(f"{BASE}/{prefix}", headers=auth_headers, json={"description": "updated"})
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "updated"
    # A create still needs the file: it would apply a reservation's settings.
    r = await client.put(f"{BASE}/lkc-{uuid.uuid4().hex[:8]}", headers=auth_headers, json={})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_the_reservation_holder_still_learns_its_reservation_is_invalid(
    client: AsyncClient, app_key, monkeypatch, tmp_path,
):
    headers, own = app_key
    _reserve(monkeypatch, tmp_path, {"prefix": own, "app": "cooking", "settings": _INVALID_ID_CONFIG})
    r = await client.put(f"{BASE}/{own}", headers=headers, json={})
    assert r.status_code == 409, r.text
    assert "cooking" in r.text


# ── requested_by ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_journal_names_the_authenticated_caller(client: AsyncClient, auth_headers: dict):
    prefix = await _deleted_namespace(client, auth_headers, deleted_by="someone-else@example.com")
    body = (await client.get(f"{BASE}/{prefix}/deletion-status", headers=auth_headers)).json()
    assert body["requested_by"] and body["requested_by"] != "someone-else@example.com"
    assert body["requested_note"] == "someone-else@example.com"
