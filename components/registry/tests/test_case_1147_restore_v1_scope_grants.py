"""A system restore of a version 1 archive keeps a key's scope reads.

Version 1 archives were made while a runtime key's namespaces list gave read
on each namespace it named, with no grant. A key's list is a scope only now,
so restoring such an archive as it stands left those keys without the reads
they had. A version 1 archive's restore writes them as read grants; a
version 2 archive (what backups write now) gets none, since there a listed
namespace without a grant is a grant that was revoked or purged.
"""

import uuid

import pytest

from registry.main import providers as _app_providers
from registry.models.api_key import StoredAPIKey
from registry.models.grant import NamespaceGrant
from registry.models.namespace import Namespace
from registry.models.reservation import RuntimeReservation
from wip_auth import APIKeyProvider

BASE = "/api/registry"
BACKUP = f"{BASE}/system-backup"
RESTORE = f"{BASE}/system-restore"


def _p(tag: str) -> str:
    return f"c1147r-{tag}-{uuid.uuid4().hex[:8]}"


def _provider() -> APIKeyProvider:
    return next(p for p in _app_providers if isinstance(p, APIKeyProvider))


async def _wipe() -> None:
    provider = _provider()
    for doc in await StoredAPIKey.find_all().to_list():
        provider.remove_key(doc.key_hash)
    await StoredAPIKey.delete_all()
    await NamespaceGrant.delete_all()
    await RuntimeReservation.delete_all()


async def _namespace(client, headers, prefix) -> None:
    r = await client.put(f"{BASE}/namespaces/{prefix}", headers=headers, json={})
    assert r.status_code == 200, r.text


async def _key(client, headers, namespaces, **body) -> tuple[str, str]:
    name = _p("key")
    r = await client.post(f"{BASE}/api-keys", headers=headers, json={
        "name": name, "groups": [], "namespaces": namespaces, **body,
    })
    assert r.status_code == 201, r.text
    return name, r.json()["plaintext_key"]


async def _backup(client, headers) -> dict:
    r = await client.post(BACKUP, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _restore(client, headers, archive, **params) -> dict:
    query = {k: str(v).lower() for k, v in params.items()}
    r = await client.post(RESTORE, json=archive, params=query, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _permission(client, plaintext, prefix) -> str:
    r = await client.get(
        f"{BASE}/my/namespaces/{prefix}/permission", headers={"X-API-Key": plaintext},
    )
    if r.status_code == 404:
        return "none"
    assert r.status_code == 200, r.text
    return r.json()["permission"]


def _as_pre_fix(archive: dict, key: str) -> dict:
    """The archive a stack from before the fix would have made: version 1,
    and no grant where the key's list gave read."""
    archive["version"] = 1
    archive["grants"] = [
        g for g in archive["grants"]
        if not (g["subject_type"] == "api_key" and g["subject"] == key and g["permission"] == "read")
    ]
    return archive


@pytest.mark.asyncio
async def test_backups_are_version_2(client, auth_headers):
    assert (await _backup(client, auth_headers))["version"] == 2


@pytest.mark.asyncio
async def test_a_version_1_keys_scope_read_comes_back_as_a_grant(client, auth_headers):
    prefix = _p("ns")
    await _namespace(client, auth_headers, prefix)
    name, plaintext = await _key(client, auth_headers, [prefix])
    archive = _as_pre_fix(await _backup(client, auth_headers), name)
    await _wipe()

    gid = f"{prefix}/api_key:{name}"
    dry = await _restore(client, auth_headers, archive)
    assert gid in dry["scope_read_grants"]
    assert gid in dry["grants"]["created"]
    assert any("version 1 archive" in w for w in dry["warnings"])
    assert await NamespaceGrant.find_one({"namespace": prefix, "subject": name}) is None

    await _restore(client, auth_headers, archive, dry_run=False)
    grant = await NamespaceGrant.find_one({"namespace": prefix, "subject": name})
    assert grant is not None and grant.permission == "read" and not grant.orphaned
    assert await _permission(client, plaintext, prefix) == "read"


@pytest.mark.asyncio
async def test_a_version_2_archive_gives_no_read_the_source_ended(client, auth_headers):
    prefix = _p("v2")
    await _namespace(client, auth_headers, prefix)
    name, plaintext = await _key(client, auth_headers, [prefix])
    archive = await _backup(client, auth_headers)
    archive["grants"] = [g for g in archive["grants"] if g["subject"] != name]  # revoked
    await _wipe()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert report["scope_read_grants"] == []
    assert await NamespaceGrant.find_one({"namespace": prefix, "subject": name}) is None
    assert await _permission(client, plaintext, prefix) == "none"


@pytest.mark.asyncio
async def test_a_namespace_the_key_holds_a_grant_on_gets_no_read_grant(client, auth_headers):
    prefix = _p("held")
    await _namespace(client, auth_headers, prefix)
    name, plaintext = await _key(client, auth_headers, [prefix], grant_permission="write")
    archive = await _backup(client, auth_headers)
    archive["version"] = 1
    await _wipe()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert not [g for g in report["scope_read_grants"] if g.endswith(f":{name}")]
    assert await _permission(client, plaintext, prefix) == "write"


@pytest.mark.asyncio
async def test_another_namespace_of_the_name_here_gets_the_grant_orphaned(client, auth_headers):
    prefix = _p("other")
    await _namespace(client, auth_headers, prefix)
    other = _p("otherkey")
    r = await client.post(f"{BASE}/namespaces/{prefix}/grants", headers=auth_headers, json=[
        {"subject": other, "subject_type": "group", "permission": "read"},
    ])
    assert r.status_code == 200, r.text
    name, plaintext = await _key(client, auth_headers, [prefix])
    archive = _as_pre_fix(await _backup(client, auth_headers), name)
    # The namespace here is another one than the archive's: same name, new id.
    ns = await Namespace.find_one({"prefix": prefix})
    ns.namespace_id = str(uuid.uuid4())
    await ns.save()
    await _wipe()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    gid = f"{prefix}/api_key:{name}"
    assert gid in report["scope_read_grants"] and gid in report["grants"]["orphaned"]
    assert await _permission(client, plaintext, prefix) == "none"


@pytest.mark.asyncio
async def test_a_namespace_with_no_recorded_id_and_not_here_gets_nothing(client, auth_headers):
    prefix = _p("gone")
    await _namespace(client, auth_headers, prefix)
    name, _plaintext = await _key(client, auth_headers, [prefix])
    archive = _as_pre_fix(await _backup(client, auth_headers), name)
    await _wipe()
    await Namespace.find_one({"prefix": prefix}).delete()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    skipped = {s["id"]: s["reason_code"] for s in report["grants"]["skipped"]}
    assert skipped[f"{prefix}/api_key:{name}"] == "scope_namespace_absent"
    assert await NamespaceGrant.find_one({"namespace": prefix, "subject": name}) is None
