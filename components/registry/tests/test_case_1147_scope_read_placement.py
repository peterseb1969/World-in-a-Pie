"""Where a version 1 archive's scope reads are written, and for whom.

A version 1 archive's restore writes a read grant for each namespace a
runtime key's list named and the archive holds no grant for. Each must land
where that namespace is, and only for the archive's own key:

- the namespace id comes from a reservation or an effective grant, never an
  orphan (an orphan's id names a namespace that did not hold the name);
- with no id recorded, a reservation here places it orphaned under the
  reservation's id (read once that namespace is created); an install's
  reservation, which holds no id yet, places nothing, and a skip says to
  restore again after the namespace;
- a key of this install that shares the name gets nothing;
- a key in a stack-wide group gets its read, as the startup backfill gave it;
- the report lists only the reads written.
"""

import uuid

import pytest

from registry.models.api_key import StoredAPIKey
from registry.models.grant import NamespaceGrant
from registry.models.namespace import Namespace
from registry.models.reservation import RuntimeReservation

from .test_case_1147_restore_v1_scope_grants import (
    BASE,
    _as_pre_fix,
    _backup,
    _key,
    _namespace,
    _permission,
    _restore,
    _wipe,
)


def _p(tag: str) -> str:
    return f"c1147p-{tag}-{uuid.uuid4().hex[:8]}"


async def _scope_key_archive(client, headers, prefix: str, **key_body) -> tuple[dict, str, str]:
    await _namespace(client, headers, prefix)
    name, plaintext = await _key(client, headers, [prefix], **key_body)
    return _as_pre_fix(await _backup(client, headers), name), name, plaintext


@pytest.mark.asyncio
async def test_an_orphans_id_does_not_place_the_scope_read(client, auth_headers):
    prefix = _p("orphan")
    archive, name, plaintext = await _scope_key_archive(client, auth_headers, prefix)
    # The source also held an orphan on the prefix, made for an earlier
    # namespace of the name: its id is not the namespace that held the name.
    archive["grants"].append({
        "namespace": prefix, "subject": _p("grp"), "subject_type": "group",
        "permission": "read", "orphaned": True, "namespace_id": str(uuid.uuid4()),
    })
    await _wipe()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    gid = f"{prefix}/api_key:{name}"
    assert gid in report["scope_read_grants"]
    assert gid not in report["grants"]["orphaned"]
    assert await _permission(client, plaintext, prefix) == "read"


@pytest.mark.asyncio
async def test_a_reservation_here_places_the_read_orphaned_under_its_id(client, auth_headers):
    prefix = _p("resv")
    archive, name, plaintext = await _scope_key_archive(client, auth_headers, prefix)
    await _wipe()
    await Namespace.find_one({"prefix": prefix}).delete()
    reserved_id = str(uuid.uuid4())
    await RuntimeReservation(prefix=prefix, namespace_id=reserved_id, app="probe").insert()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    gid = f"{prefix}/api_key:{name}"
    assert gid in report["scope_read_grants"] and gid in report["grants"]["orphaned"]
    grant = await NamespaceGrant.find_one({"namespace": prefix, "subject": name})
    assert grant.orphaned and grant.namespace_id == reserved_id

    # The reserved namespace is created: it takes the reservation's id, and
    # the read becomes effective.
    await _namespace(client, auth_headers, prefix)
    assert await _permission(client, plaintext, prefix) == "read"


@pytest.mark.asyncio
async def test_an_install_reservation_with_no_id_places_nothing(client, auth_headers):
    prefix = _p("install")
    archive, name, _plaintext = await _scope_key_archive(client, auth_headers, prefix)
    await _wipe()
    await Namespace.find_one({"prefix": prefix}).delete()
    await RuntimeReservation(
        prefix=prefix, namespace_id=None, app="probe", managed_by="wip-deploy", origin="file",
    ).insert()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    gid = f"{prefix}/api_key:{name}"
    skipped = {s["id"]: s for s in report["grants"]["skipped"]}
    assert skipped[gid]["reason_code"] == "scope_namespace_absent"
    assert "again" in skipped[gid]["reason"]
    assert gid not in report["scope_read_grants"]
    assert await NamespaceGrant.find_one({"namespace": prefix, "subject": name}) is None


@pytest.mark.asyncio
async def test_a_skipped_scope_read_is_not_reported_written(client, auth_headers):
    prefix = _p("absent")
    archive, name, _plaintext = await _scope_key_archive(client, auth_headers, prefix)
    await _wipe()
    await Namespace.find_one({"prefix": prefix}).delete()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    gid = f"{prefix}/api_key:{name}"
    skipped = {s["id"]: s for s in report["grants"]["skipped"]}
    assert skipped[gid]["reason_code"] == "scope_namespace_absent"
    assert "again" in skipped[gid]["reason"]
    assert gid not in report["scope_read_grants"]


@pytest.mark.asyncio
async def test_a_key_of_this_install_sharing_the_name_gets_no_scope_read(client, auth_headers):
    prefix = _p("shared")
    archive, name, _plaintext = await _scope_key_archive(client, auth_headers, prefix)
    await _wipe()
    # This install's deployer manages a key of the same name, with no
    # declared grants: the restore leaves it alone, and must not give it read.
    await StoredAPIKey(
        name=name, key_hash="$2b$12$" + "x" * 53, namespaces=[prefix],
        managed_by="wip-deploy", origin="file", grants_managed=False,
    ).insert()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    gid = f"{prefix}/api_key:{name}"
    skipped = {s["id"]: s["reason_code"] for s in report["grants"]["skipped"]}
    assert skipped[gid] == "scope_key_not_restored"
    assert gid not in report["scope_read_grants"]
    assert await NamespaceGrant.find_one({"namespace": prefix, "subject": name}) is None


@pytest.mark.asyncio
async def test_a_stack_wide_keys_scope_read_comes_back(client, auth_headers):
    prefix = _p("svc")
    archive, name, plaintext = await _scope_key_archive(
        client, auth_headers, prefix, groups=["wip-services"],
    )
    await _wipe()

    report = await _restore(client, auth_headers, archive, dry_run=False)
    gid = f"{prefix}/api_key:{name}"
    assert gid in report["scope_read_grants"]
    grant = await NamespaceGrant.find_one({"namespace": prefix, "subject": name})
    assert grant is not None and grant.permission == "read" and not grant.orphaned
    r = await client.get(
        f"{BASE}/my/namespaces/{prefix}/permission", headers={"X-API-Key": plaintext},
    )
    assert r.status_code == 200 and r.json()["permission"] in ("read", "write", "admin")


@pytest.mark.asyncio
async def test_the_same_key_with_a_differing_record_keeps_its_scope_read(client, auth_headers):
    prefix = _p("differs")
    archive, name, plaintext = await _scope_key_archive(client, auth_headers, prefix)
    # The key is still here, the same key (same hash), its record edited since
    # the backup: an upsert leaves it as it is ("differs"), and it is still the
    # archive's key, so it keeps the read its list gave.
    await NamespaceGrant.find_one({"namespace": prefix, "subject": name}).delete()
    r = await client.patch(f"{BASE}/api-keys/{name}", headers=auth_headers,
                           json={"description": "edited after the backup"})
    assert r.status_code == 200, r.text

    report = await _restore(client, auth_headers, archive, dry_run=False)
    assert name in {s["name"] for s in report["api_keys"]["skipped"] if s["reason_code"] == "differs"}
    gid = f"{prefix}/api_key:{name}"
    assert gid in report["scope_read_grants"]
    assert await _permission(client, plaintext, prefix) == "read"
