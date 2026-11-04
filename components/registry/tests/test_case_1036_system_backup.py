"""System backup and restore of the Registry's auth state.

A system backup carries every stored API key (hash, never plaintext), every
namespace grant and every reservation. A restore upserts by identity, never
deletes, runs as a dry run unless told otherwise, refuses another hash salt
on a stack with keys of its own,
and brings every record back unmanaged (origin "restored"); records the
install's files own on the target are never replaced. User grants follow the
OIDC issuer, and a grant without its namespace is kept orphaned.
"""

import uuid

import pytest

from registry.main import providers as _app_providers
from registry.models.api_key import StoredAPIKey
from registry.models.grant import NamespaceGrant
from registry.models.reservation import RuntimeReservation
from registry.services import key_shadowing
from registry.services.system_backup import salt_fingerprint
from wip_auth import APIKeyProvider
from wip_auth.providers.api_key import hash_api_key

KEYS = "/api/registry/api-keys"
BACKUP = "/api/registry/system-backup"
RESTORE = "/api/registry/system-restore"


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


def _provider() -> APIKeyProvider:
    return next(p for p in _app_providers if isinstance(p, APIKeyProvider))


@pytest.fixture
async def fresh(client):
    """Grants are not cleared by the shared fixture; these tests count them."""
    await NamespaceGrant.delete_all()
    key_shadowing.reset_for_tests()
    yield client
    key_shadowing.reset_for_tests()


async def _wipe(provider: APIKeyProvider) -> None:
    for doc in await StoredAPIKey.find_all().to_list():
        provider.remove_key(doc.key_hash)
    await StoredAPIKey.delete_all()
    await NamespaceGrant.delete_all()
    await RuntimeReservation.delete_all()


async def _backup(client, headers) -> dict:
    resp = await client.post(BACKUP, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _restore(client, headers, archive, **params) -> dict:
    query = {k: str(v).lower() for k, v in params.items()}
    resp = await client.post(RESTORE, json=archive, params=query, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _make_state(client, headers) -> tuple[str, str, str]:
    """A Console key with a write grant on 'default' and a reservation, a group
    grant and a user grant. Returns (key name, its plaintext, reserved prefix)."""
    name, prefix = _p("rc-key"), _p("resv")
    resp = await client.post(KEYS, headers=headers, json={
        "name": name, "namespaces": ["default", prefix], "grant_permission": "write",
        "reserve": [{"prefix": prefix}],
    })
    assert resp.status_code == 201, resp.text
    plaintext = resp.json()["plaintext_key"]
    resp = await client.post("/api/registry/namespaces/vendor1/grants", headers=headers, json=[
        {"subject": "analysts", "subject_type": "group", "permission": "read"},
        {"subject": "ann@example.org", "subject_type": "user", "permission": "write"},
    ])
    assert resp.status_code == 200, resp.text
    assert all(r["status"] == "created" for r in resp.json()["results"])
    return name, plaintext, prefix


async def _authenticates(client, plaintext: str) -> bool:
    resp = await client.get("/api/registry/my/namespaces", headers={"X-API-Key": plaintext})
    return resp.status_code == 200


# --- round trip ------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_trip_after_a_wipe(fresh, auth_headers):
    client = fresh
    name, plaintext, prefix = await _make_state(client, auth_headers)
    archive = await _backup(client, auth_headers)

    assert archive["format"] == "wip-system-backup" and archive["version"] == 1
    assert archive["source"]["salt_fingerprint"].startswith("sha256:")
    assert [k["name"] for k in archive["api_keys"]] == [name]
    assert plaintext not in str(archive)

    await _wipe(_provider())
    assert not await _authenticates(client, plaintext)

    # A dry run (the default) writes nothing.
    report = await _restore(client, auth_headers, archive)
    assert report["dry_run"] is True
    assert report["api_keys"]["created"] == [name]
    assert report["reservations"]["created"] == [prefix]
    assert f"{prefix}/api_key:{name}" in report["grants"]["created"]
    assert await StoredAPIKey.find_one(StoredAPIKey.name == name) is None

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert report["dry_run"] is False
    assert report["api_keys"]["created"] == [name]
    # The admin grant on the reserved prefix comes back although the namespace
    # does not exist yet: the key needs it to create the namespace.
    assert sorted(report["grants"]["created"]) == sorted([
        f"default/api_key:{name}", f"{prefix}/api_key:{name}",
        "vendor1/group:analysts", "vendor1/user:ann@example.org",
    ])
    assert report["grants"]["skipped"] == []

    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    assert doc.origin == "restored" and doc.managed_by is None
    reservation = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    assert reservation.app == name and reservation.origin == "restored"
    grant = await NamespaceGrant.find_one({"namespace": "default", "subject": name})
    assert grant.permission == "write" and grant.origin == "restored"
    # The Console key authenticates again, with the app's own plaintext, and
    # can still bootstrap the namespace it reserves.
    assert await _authenticates(client, plaintext)
    resp = await client.put(f"/api/registry/namespaces/{prefix}", json={},
                            headers={"X-API-Key": plaintext})
    assert resp.status_code == 200, resp.text

    # Re-running changes nothing.
    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert report["api_keys"]["created"] == [] and report["api_keys"]["unchanged"] == [name]
    assert report["grants"]["created"] == []


@pytest.mark.asyncio
async def test_backup_and_restore_need_a_platform_admin(fresh, auth_headers):
    client = fresh
    _, plaintext, _ = await _make_state(client, auth_headers)
    assert (await client.post(BACKUP, headers={"X-API-Key": plaintext})).status_code == 403
    archive = await _backup(client, auth_headers)
    resp = await client.post(RESTORE, json=archive, headers={"X-API-Key": plaintext})
    assert resp.status_code == 403


# --- refusals and skips ----------------------------------------------------


@pytest.mark.asyncio
async def test_another_salt_is_refused_by_a_stack_with_keys_of_its_own(fresh, auth_headers):
    client = fresh
    await _make_state(client, auth_headers)
    archive = await _backup(client, auth_headers)
    archive["source"]["salt"] = "another-stack-salt"
    archive["source"]["salt_fingerprint"] = salt_fingerprint("another-stack-salt")
    resp = await client.post(RESTORE, json=archive, params={"dry_run": "false"}, headers=auth_headers)
    assert resp.status_code == 409
    assert "keys of its own" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_user_grants_from_another_issuer_need_the_flag(fresh, auth_headers):
    client = fresh
    await _make_state(client, auth_headers)
    archive = await _backup(client, auth_headers)
    archive["source"]["oidc_issuer"] = "https://elsewhere.example/dex"
    await NamespaceGrant.delete_all()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    skipped = {s["id"]: s["reason"] for s in report["grants"]["skipped"]}
    assert "another OIDC issuer" in skipped["vendor1/user:ann@example.org"]
    assert "vendor1/group:analysts" in report["grants"]["created"]
    assert any("issuer" in w for w in report["warnings"])
    assert await NamespaceGrant.find_one({"subject": "ann@example.org"}) is None

    report = await _restore(client, auth_headers, archive, dry_run=False, include_user_grants=True)
    assert report["grants"]["created"] == ["vendor1/user:ann@example.org"]


@pytest.mark.asyncio
async def test_a_grant_without_its_namespace_is_written_orphaned(fresh, auth_headers):
    """The full orphan lifecycle is in test_case_1053_orphan_grants.py."""
    client = fresh
    await _make_state(client, auth_headers)
    archive = await _backup(client, auth_headers)
    archive["grants"].append({**archive["grants"][0], "namespace": "later-ns",
                              "subject": "late-group", "subject_type": "group"})
    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert report["grants"]["created"] == ["later-ns/group:late-group"]
    assert report["grants"]["orphaned"] == ["later-ns/group:late-group"]
    assert report["grants"]["skipped"] == []


@pytest.mark.asyncio
async def test_managed_source_records_arrive_unmanaged(fresh, auth_headers):
    client = fresh
    archive = await _backup(client, auth_headers)
    archive["api_keys"].append({
        "name": "deployed-app", "key_hash": hash_api_key("deployed-plaintext"),
        "owner": "app:deployed-app", "groups": [], "namespaces": ["default"],
        "managed_by": "wip-deploy", "origin": "file",
    })
    archive["grants"].append({
        "namespace": "default", "subject": "deployed-app", "subject_type": "api_key",
        "permission": "write", "managed_by": "wip-deploy", "origin": "file",
    })
    await _restore(client, auth_headers, archive, dry_run=False)
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == "deployed-app")
    assert doc.managed_by is None and doc.origin == "restored" and not doc.grants_managed
    grant = await NamespaceGrant.find_one({"subject": "deployed-app"})
    assert grant.managed_by is None and grant.origin == "restored"
    assert await _authenticates(client, "deployed-plaintext")


@pytest.mark.asyncio
async def test_records_the_files_own_here_are_never_replaced(fresh, auth_headers):
    client = fresh
    name, _, prefix = await _make_state(client, auth_headers)
    archive = await _backup(client, auth_headers)

    # The target's key file now manages a key of that name, with its grants,
    # and reserves the prefix.
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    await doc.set({"managed_by": "wip-deploy", "origin": "file", "grants_managed": True,
                   "key_hash": hash_api_key("the-file-plaintext")})
    await NamespaceGrant.find({"subject": name}).delete()
    reservation = await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)
    await reservation.set({"managed_by": "wip-deploy", "origin": "file", "app": "the-app"})

    report = await _restore(client, auth_headers, archive, dry_run=False, overwrite=True)
    reasons = {s["id"]: s["reason"] for part in ("api_keys", "grants", "reservations")
               for s in report[part]["skipped"]}
    assert "managed by wip-deploy" in reasons[name]
    assert "managed by wip-deploy" in reasons[prefix]
    assert "managed by wip-deploy" in reasons[f"default/api_key:{name}"]
    doc = await StoredAPIKey.find_one(StoredAPIKey.name == name)
    assert doc.managed_by == "wip-deploy"
    assert await NamespaceGrant.find_one({"subject": name}) is None


@pytest.mark.asyncio
async def test_a_key_named_like_a_config_key_is_skipped(fresh, auth_headers):
    client = fresh
    config_name = next(iter(_provider().config_key_names()))
    archive = await _backup(client, auth_headers)
    archive["api_keys"].append({"name": config_name, "key_hash": hash_api_key("x")})
    report = await _restore(client, auth_headers, archive, dry_run=False)
    reasons = {s["id"]: s["reason"] for s in report["api_keys"]["skipped"]}
    assert "config key" in reasons[config_name]
    assert await StoredAPIKey.find_one(StoredAPIKey.name == config_name) is None


# --- overwrite, never delete, the warning ----------------------------------


@pytest.mark.asyncio
async def test_a_different_record_is_replaced_only_with_overwrite(fresh, auth_headers):
    client = fresh
    await _make_state(client, auth_headers)
    archive = await _backup(client, auth_headers)
    grant = await NamespaceGrant.find_one({"namespace": "vendor1", "subject": "analysts"})
    await grant.set({"permission": "admin"})

    report = await _restore(client, auth_headers, archive, dry_run=False)
    reasons = {s["id"]: s["reason"] for s in report["grants"]["skipped"]}
    assert "overwrite" in reasons["vendor1/group:analysts"]
    assert (await NamespaceGrant.find_one({"subject": "analysts"})).permission == "admin"

    report = await _restore(client, auth_headers, archive, dry_run=False, overwrite=True)
    assert report["grants"]["replaced"] == ["vendor1/group:analysts"]
    assert (await NamespaceGrant.find_one({"subject": "analysts"})).permission == "read"


@pytest.mark.asyncio
async def test_a_restore_never_deletes_and_warns_on_a_used_instance(fresh, auth_headers):
    client = fresh
    archive = await _backup(client, auth_headers)  # an empty archive
    name, plaintext, prefix = await _make_state(client, auth_headers)

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert any("revoked since the backup" in w for w in report["warnings"])
    assert await StoredAPIKey.find_one(StoredAPIKey.name == name) is not None
    assert await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix) is not None
    assert await NamespaceGrant.find_one({"subject": "analysts"}) is not None
    assert await _authenticates(client, plaintext)


@pytest.mark.asyncio
async def test_a_fresh_instance_gets_no_warning(fresh, auth_headers):
    client = fresh
    await _make_state(client, auth_headers)
    archive = await _backup(client, auth_headers)
    await _wipe(_provider())
    report = await _restore(client, auth_headers, archive)
    assert report["warnings"] == []


@pytest.mark.asyncio
async def test_a_managed_grant_on_an_absent_namespace_reads_as_managed(fresh, auth_headers):
    """The import writes a key's declared grants whether or not the namespace
    exists; such a grant is the file's, not one waiting for its namespace."""
    client = fresh
    await NamespaceGrant(namespace="never-created", subject="file-key", subject_type="api_key",
                         permission="read", granted_by="wip-deploy", managed_by="wip-deploy",
                         origin="file").insert()
    archive = await _backup(client, auth_headers)
    report = await _restore(client, auth_headers, archive)
    reasons = {s["id"]: s["reason"] for s in report["grants"]["skipped"]}
    assert reasons["never-created/api_key:file-key"] == "managed by wip-deploy on this instance"
