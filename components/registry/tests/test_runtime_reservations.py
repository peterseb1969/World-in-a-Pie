"""Runtime namespace reservations: an app key for an app wip-deploy does not manage.

A platform admin creates a runtime key that reserves its namespaces, with
settings the admin fixes. The key gets `admin` on each and can create it
through the namespace upsert, exactly like a deployer-managed app key with a
file reservation. One record holds each prefix: a runtime reservation of a
prefix the install's file reserves is refused (managed_by_deployer), and a
file reservation of a prefix a runtime one holds replaces it. Narrowing or
revoking releases a reservation whose namespace was never created and keeps
(and reports) one whose namespace exists.
"""

import json
import uuid

import pytest
from httpx import AsyncClient

from registry.models.namespace import Namespace
from registry.models.reservation import RuntimeReservation
from registry.services.reservations import RESERVATIONS_FILE_ENV

KEYS = "/api/registry/api-keys"
NS = "/api/registry/namespaces"
RES = "/api/registry/reservations"

SETTINGS = {"description": "External app", "isolation_mode": "strict",
            "allowed_external_refs": ["wip"], "deletion_mode": "full"}


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


def _file(monkeypatch, tmp_path, *reservations: dict) -> None:
    path = tmp_path / "namespace-reservations.json"
    path.write_text(json.dumps({"reservations": list(reservations)}))
    monkeypatch.setenv(RESERVATIONS_FILE_ENV, str(path))


async def _app_key(client, headers, name, prefix, settings=None, **extra):
    body = {"name": name, "namespaces": [prefix],
            "reserve": [{"prefix": prefix, "settings": settings or {}}], **extra}
    return await client.post(KEYS, json=body, headers=headers)


async def _grants(client, headers, prefix) -> dict[str, str]:
    data = (await client.get(f"{NS}/{prefix}/grants", headers=headers)).json()
    items = data if isinstance(data, list) else data["items"]
    return {g["subject"]: g["permission"] for g in items}


@pytest.mark.asyncio
async def test_a_reserving_key_bootstraps_its_namespace_as_reserved(
    client: AsyncClient, auth_headers: dict,
):
    prefix = _p("ext")
    resp = await _app_key(client, auth_headers, _p("key"), prefix, SETTINGS)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["reserved_namespaces"] == [prefix]
    key = {"X-API-Key": body["plaintext_key"]}
    assert (await _grants(client, auth_headers, prefix))[body["name"]] == "admin"

    created = await client.put(f"{NS}/{prefix}", json={}, headers=key)
    assert created.status_code == 200, created.text
    for field, value in SETTINGS.items():
        assert created.json()[field] == value, field

    # Re-running the bootstrap is a no-op; a different setting is refused.
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=key)).status_code == 200
    other = await client.put(f"{NS}/{prefix}", json={"deletion_mode": "retain"}, headers=key)
    assert other.status_code == 403


@pytest.mark.asyncio
async def test_an_admin_grant_without_a_reservation_still_cannot_bootstrap(
    client: AsyncClient, auth_headers: dict,
):
    prefix = _p("plain")
    resp = await client.post(KEYS, json={"name": _p("key"), "namespaces": [prefix],
                                         "grant_permission": "admin"}, headers=auth_headers)
    key = {"X-API-Key": resp.json()["plaintext_key"]}
    assert (await client.put(f"{NS}/{prefix}", json={}, headers=key)).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix,expected", [("wip", 400), ("Bad Prefix", 400)])
async def test_unreservable_prefixes_are_refused(
    client: AsyncClient, auth_headers: dict, prefix: str, expected: int,
):
    name = _p("key")
    resp = await _app_key(client, auth_headers, name, prefix)
    assert resp.status_code == expected, resp.text
    # Nothing was written: no key, no reservation.
    assert (await client.get(f"{KEYS}/{name}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_reserve_must_be_within_the_keys_namespaces(client: AsyncClient, auth_headers: dict):
    resp = await client.post(KEYS, json={"name": _p("key"), "namespaces": ["a-ns"],
                                         "reserve": [{"prefix": "b-ns"}]}, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_a_prefix_has_one_owner(
    client: AsyncClient, auth_headers: dict, monkeypatch, tmp_path,
):
    prefix = _p("owned")
    assert (await _app_key(client, auth_headers, _p("first"), prefix)).status_code == 201
    second = await _app_key(client, auth_headers, _p("second"), prefix)
    assert second.status_code == 409
    assert "already reserved by runtime API key" in second.json()["detail"]

    in_file = _p("deployed")
    _file(monkeypatch, tmp_path, {"prefix": in_file, "app": "cooking"})
    refused = await _app_key(client, auth_headers, _p("third"), in_file)
    assert refused.status_code == 409
    assert refused.json()["error_code"] == "managed_by_deployer"
    assert "managed by wip-deploy" in refused.json()["detail"]


@pytest.mark.asyncio
async def test_an_id_prefix_is_reserved_once(client: AsyncClient, auth_headers: dict):
    cfg = {"id_config": {"terms": {"algorithm": "prefixed", "prefix": "EXT-", "pad": 6}}}
    tag = uuid.uuid4().hex[:4].upper()
    cfg["id_config"]["terms"]["prefix"] = f"X{tag}-"
    assert (await _app_key(client, auth_headers, _p("a"), _p("ida"), cfg)).status_code == 201
    clash = await _app_key(client, auth_headers, _p("b"), _p("idb"), cfg)
    assert clash.status_code == 409, clash.text
    assert "already reserved for namespace" in clash.json()["detail"]


@pytest.mark.asyncio
async def test_widen_with_a_reservation_then_narrow_releases_it(
    client: AsyncClient, auth_headers: dict,
):
    name, home, extra = _p("key"), _p("home"), _p("extra")
    created = await client.post(KEYS, json={"name": name, "namespaces": [home]}, headers=auth_headers)
    key = {"X-API-Key": created.json()["plaintext_key"]}

    widened = await client.put(f"{KEYS}/{name}/namespaces/{extra}",
                               json={"reserve": SETTINGS}, headers=auth_headers)
    assert widened.status_code == 200, widened.text
    assert extra in widened.json()["namespaces"]
    assert widened.json()["reserved_namespaces"] == [extra]
    assert (await _grants(client, auth_headers, extra))[name] == "admin"

    narrowed = await client.delete(f"{KEYS}/{name}/namespaces/{extra}", headers=auth_headers)
    assert narrowed.status_code == 200
    assert narrowed.json()["reservation"] == "released"
    assert extra not in narrowed.json()["namespaces"]
    assert name not in await _grants(client, auth_headers, extra)
    # The key can no longer create it.
    assert (await client.put(f"{NS}/{extra}", json={}, headers=key)).status_code == 403


@pytest.mark.asyncio
async def test_widen_without_reserving_grants_read_by_default(client: AsyncClient, auth_headers: dict):
    name, home, shared = _p("key"), _p("home"), _p("shared")
    await client.post(KEYS, json={"name": name, "namespaces": [home]}, headers=auth_headers)
    widened = await client.put(f"{KEYS}/{name}/namespaces/{shared}", json={}, headers=auth_headers)
    assert widened.status_code == 200
    assert widened.json()["reserved_namespaces"] == []
    assert (await _grants(client, auth_headers, shared))[name] == "read"
    refused = await client.put(f"{KEYS}/{name}/namespaces/{shared}",
                               json={"reserve": {}, "permission": "read"}, headers=auth_headers)
    assert refused.status_code == 422


@pytest.mark.asyncio
async def test_revoke_releases_uncreated_reservations_and_keeps_created_ones(
    client: AsyncClient, auth_headers: dict,
):
    name, made, unmade = _p("key"), _p("made"), _p("unmade")
    resp = await client.post(KEYS, json={
        "name": name, "namespaces": [made, unmade],
        "reserve": [{"prefix": made}, {"prefix": unmade}],
    }, headers=auth_headers)
    key = {"X-API-Key": resp.json()["plaintext_key"]}
    assert (await client.put(f"{NS}/{made}", json={}, headers=key)).status_code == 200

    revoked = await client.delete(f"{KEYS}/{name}", headers=auth_headers)
    assert revoked.status_code == 200
    body = revoked.json()
    assert body["released_reservations"] == [unmade]
    assert body["kept_reservations"] == [made]
    assert sorted(body["revoked_grants"]) == sorted([made, unmade])
    # The namespace is never deleted as a side effect.
    assert await Namespace.find_one(Namespace.prefix == made) is not None
    assert await RuntimeReservation.find_one(RuntimeReservation.prefix == unmade) is None

    # A kept reservation is released explicitly; the namespace stays.
    released = await client.delete(f"{RES}/{made}", headers=auth_headers)
    assert released.status_code == 200
    assert await Namespace.find_one(Namespace.prefix == made) is not None


@pytest.mark.asyncio
async def test_patch_cannot_drop_a_reserved_namespace(client: AsyncClient, auth_headers: dict):
    name, prefix = _p("key"), _p("res")
    await _app_key(client, auth_headers, name, prefix)
    resp = await client.patch(f"{KEYS}/{name}", json={"namespaces": []}, headers=auth_headers)
    assert resp.status_code == 409
    assert "/namespaces/" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_a_file_reservation_replaces_a_runtime_one_of_the_same_prefix(
    client: AsyncClient, auth_headers: dict, monkeypatch, tmp_path,
):
    prefix = _p("both")
    key_name = _p("key")
    created = await _app_key(client, auth_headers, key_name, prefix)
    assert created.status_code == 201
    # A later apply rendered a file reservation of the same prefix: the
    # install's reservation wins, and the listing shows one record.
    _file(monkeypatch, tmp_path, {"prefix": prefix, "app": "cooking"})

    listed = [r for r in (await client.get(RES, headers=auth_headers)).json() if r["prefix"] == prefix]
    assert [(r["source"], r["app"], r["managed_by"]) for r in listed] == [
        ("file", "cooking", "wip-deploy")
    ]
    assert listed[0]["conflict"] is None

    # The key that made the runtime one no longer reserves it, keeps its
    # grant, and can still create the namespace (at the file's settings).
    key = (await client.get(f"{KEYS}/{key_name}", headers=auth_headers)).json()
    assert key["reserved_namespaces"] == []
    assert (await _grants(client, auth_headers, prefix))[key_name] == "admin"
    upsert = await client.put(
        f"{NS}/{prefix}", json={}, headers={"X-API-Key": created.json()["plaintext_key"]}
    )
    assert upsert.status_code == 200, upsert.text

    runtime_only = (await client.get(f"{RES}?source=runtime", headers=auth_headers)).json()
    assert all(r["source"] == "runtime" for r in runtime_only)


@pytest.mark.asyncio
async def test_release_endpoint_refuses_file_reservations_and_unknown_prefixes(
    client: AsyncClient, auth_headers: dict, monkeypatch, tmp_path,
):
    in_file = _p("deployed")
    _file(monkeypatch, tmp_path, {"prefix": in_file, "app": "cooking"})
    refused = await client.delete(f"{RES}/{in_file}", headers=auth_headers)
    assert refused.status_code == 409
    assert refused.json()["error_code"] == "managed_by_deployer"
    assert (await client.delete(f"{RES}/{_p('none')}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_reservation_endpoints_are_admin_only(client: AsyncClient, auth_headers: dict):
    resp = await client.post(KEYS, json={"name": _p("key"), "namespaces": ["x"]}, headers=auth_headers)
    key = {"X-API-Key": resp.json()["plaintext_key"]}
    assert (await client.get(RES, headers=key)).status_code in (401, 403)
    assert (await client.delete(f"{RES}/x", headers=key)).status_code in (401, 403)
