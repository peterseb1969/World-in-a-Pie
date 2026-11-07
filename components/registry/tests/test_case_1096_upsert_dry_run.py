"""PUT /namespaces/{prefix}?dry_run=true checks everything and writes nothing.

Provenance: CASE-1096 part 5. A restore's dry run skipped creating the target
namespace, the one step that runs as the requester, so it passed requests the
real restore then refused. With dry_run the upsert makes every check the real
call makes and answers with the namespace as it would be stored.
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
    """A config-file key with admin on one namespace and no groups: the key an
    app holds. Yields (headers, prefix)."""
    from registry.api import api_keys as api_keys_module
    from wip_auth.models import APIKeyRecord
    from wip_auth.providers.api_key import hash_api_key

    prefix = f"res-{uuid.uuid4().hex[:8]}"
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


def _reserve(monkeypatch, tmp_path, *reservations: dict) -> None:
    path = tmp_path / "namespace-reservations.json"
    path.write_text(json.dumps({"reservations": list(reservations)}))
    monkeypatch.setenv(RESERVATIONS_FILE_ENV, str(path))


@pytest.mark.asyncio
async def test_a_dry_create_answers_without_creating(client: AsyncClient, auth_headers: dict):
    prefix = f"dry-{uuid.uuid4().hex[:8]}"
    resp = await client.put(
        f"{BASE}/{prefix}", params={"dry_run": "true"}, headers=auth_headers,
        json={"description": "would be", "isolation_mode": "strict"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["isolation_mode"] == "strict"
    assert await Namespace.find_one({"prefix": prefix}) is None


@pytest.mark.asyncio
async def test_a_dry_update_answers_without_changing(client: AsyncClient, auth_headers: dict):
    prefix = f"dry-{uuid.uuid4().hex[:8]}"
    created = await client.put(f"{BASE}/{prefix}", headers=auth_headers, json={"description": "kept"})
    assert created.status_code == 200, created.text

    resp = await client.put(
        f"{BASE}/{prefix}", params={"dry_run": "true"}, headers=auth_headers,
        json={"description": "would change"},
    )
    assert resp.status_code == 200 and resp.json()["description"] == "would change"
    assert (await Namespace.find_one({"prefix": prefix})).description == "kept"


@pytest.mark.asyncio
async def test_a_dry_run_refuses_what_the_real_call_refuses(
    client: AsyncClient, app_key, monkeypatch, tmp_path
):
    headers, prefix = app_key
    _reserve(monkeypatch, tmp_path, {"prefix": "someone-else", "app": "other"})
    dry = await client.put(f"{BASE}/{prefix}", params={"dry_run": "true"}, headers=headers, json={})
    real = await client.put(f"{BASE}/{prefix}", headers=headers, json={})
    assert dry.status_code == real.status_code == 403
    assert await Namespace.find_one({"prefix": prefix}) is None


@pytest.mark.asyncio
async def test_a_dry_create_by_a_reservation_holder_is_allowed(
    client: AsyncClient, app_key, monkeypatch, tmp_path
):
    headers, prefix = app_key
    _reserve(monkeypatch, tmp_path, {"prefix": prefix, "app": "cooking", "settings": {"isolation_mode": "strict"}})
    resp = await client.put(f"{BASE}/{prefix}", params={"dry_run": "true"}, headers=headers, json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["isolation_mode"] == "strict"
    assert await Namespace.find_one({"prefix": prefix}) is None
