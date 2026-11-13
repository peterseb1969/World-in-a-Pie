"""What the review of the install-reservation change (4a479b7f) found.

- A grant given while no namespace of its name existed (a key-file grant, a
  key created listing a namespace not yet created) is archived with no
  namespace id. A system restore wrote it effective onto any namespace of
  that name on the target, an unrelated one included. It is skipped there
  now (`unrelated_namespace_here`); where no namespace of the name is here it
  is still written effective, for the namespace that takes the name.
- An install's reservation of an absent namespace was archived with a null
  id and restored as a runtime reservation with an id of its own, which
  refused the namespace restore that followed (409), and the null made the
  archive unreadable to an older Registry. Backups leave it out now; an
  archive that carries one has it skipped (`managed_on_source`).
- The flow the case was filed for keeps the app's key: after the namespace
  is deleted and restored under its archived id, the key-file grant is there
  and effective.
"""

import json
import uuid

import pytest

from registry.models.grant import NamespaceGrant
from registry.models.namespace import Namespace
from registry.models.reservation import RuntimeReservation
from registry.services import key_import
from registry.services.key_import import KEY_FILE_ENV

from .test_case_1033_key_import import CANARY, _app_entry
from .test_case_1303_install_reservation_id import (
    BACKUP,
    KEYS,
    NS,
    RESTORE,
    _delete,
    _p,
    _reserved,
    _uuid,
    install_reserves,  # noqa: F401  (fixture)
)


async def _backup(client, headers) -> dict:
    resp = await client.post(BACKUP, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _restore(client, headers, archive: dict) -> dict:
    resp = await client.post(RESTORE, json=archive, params={"dry_run": "false"}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _skipped(report: dict, kind: str) -> dict[str, str]:
    return {s["id"]: s["reason_code"] for s in report[kind]["skipped"]}


@pytest.mark.asyncio
async def test_an_id_less_grant_does_not_land_on_an_unrelated_namespace(
    client, auth_headers, install_reserves,  # noqa: F811
):
    prefix = _p("kb")
    await install_reserves(prefix)
    key = _p("early")
    # A key created listing the reserved name before any namespace of it
    # exists: its grant holds no namespace, and is archived with no id.
    resp = await client.post(KEYS, headers=auth_headers, json={"name": key, "namespaces": [prefix]})
    assert resp.status_code == 201, resp.text
    archive = await _backup(client, auth_headers)
    archived = next(g for g in archive["grants"] if g["namespace"] == prefix and g["subject"] == key)
    assert archived["namespace_id"] is None

    # On the target, a namespace of that name exists: not the one it was given for.
    await NamespaceGrant.find({"namespace": prefix, "subject": key}).delete()
    created = await client.put(f"{NS}/{prefix}", headers=auth_headers, json={})
    assert created.status_code == 200, created.text
    report = await _restore(client, auth_headers, archive)

    assert _skipped(report, "grants")[f"{prefix}/api_key:{key}"] == "unrelated_namespace_here"
    assert await NamespaceGrant.find_one({"namespace": prefix, "subject": key}) is None


@pytest.mark.asyncio
async def test_an_id_less_grant_still_waits_for_a_namespace_not_yet_here(
    client, auth_headers, install_reserves,  # noqa: F811
):
    prefix = _p("kb")
    await install_reserves(prefix)
    key = _p("early")
    resp = await client.post(KEYS, headers=auth_headers, json={"name": key, "namespaces": [prefix]})
    assert resp.status_code == 201, resp.text
    archive = await _backup(client, auth_headers)
    await NamespaceGrant.find({"namespace": prefix, "subject": key}).delete()

    report = await _restore(client, auth_headers, archive)
    gid = f"{prefix}/api_key:{key}"
    assert gid not in _skipped(report, "grants")
    grant = await NamespaceGrant.find_one({"namespace": prefix, "subject": key})
    assert grant is not None and not grant.orphaned


@pytest.mark.asyncio
async def test_a_backup_leaves_out_an_install_reservation_holding_no_id(
    client, auth_headers, install_reserves,  # noqa: F811
):
    absent, present = _p("kb"), _p("lib")
    await install_reserves(absent, present)
    created = await client.put(f"{NS}/{present}", headers=auth_headers, json={})
    assert created.status_code == 200, created.text

    prefixes = {r["prefix"]: r for r in (await _backup(client, auth_headers))["reservations"]}
    assert absent not in prefixes
    assert prefixes[present]["namespace_id"] == created.json()["namespace_id"]


@pytest.mark.asyncio
async def test_an_archived_null_reservation_is_skipped_and_the_restore_after_it_works(
    client, auth_headers,
):
    """An archive a develop build wrote with the null: the reservation is not
    written as a runtime one, so the namespace restore that follows keeps its
    archived id instead of meeting a 409."""
    prefix = _p("kb")
    archive = await _backup(client, auth_headers)
    archive["reservations"].append({
        "prefix": prefix, "app": "wip-kb", "namespace_id": None, "managed_by": "wip-deploy",
    })
    report = await _restore(client, auth_headers, archive)
    assert _skipped(report, "reservations")[prefix] == "managed_on_source"
    assert await _reserved(prefix) is None

    archived_id = _uuid()
    restored = await client.put(f"{NS}/{prefix}", headers=auth_headers, json={"namespace_id": archived_id})
    assert restored.status_code == 200, restored.text
    assert restored.json()["namespace_id"] == archived_id


@pytest.mark.asyncio
async def test_the_apps_key_keeps_admin_through_delete_and_restore(
    client, auth_headers, install_reserves, monkeypatch, tmp_path,  # noqa: F811
):
    prefix, archived = _p("kb"), _uuid()
    app_key = _p("wip-kb")
    path = tmp_path / "api-keys.json"
    path.write_text(json.dumps({"keys": [
        CANARY, _app_entry(app_key, f"plain-{uuid.uuid4().hex}", [prefix], {prefix: "admin"}),
    ]}))
    monkeypatch.setenv(KEY_FILE_ENV, str(path))
    await install_reserves(prefix)
    created = await client.put(f"{NS}/{prefix}", headers=auth_headers, json={})
    assert created.status_code == 200, created.text

    await _delete(client, auth_headers, prefix)
    await key_import.run_pass()  # the install's next import
    restored = await client.put(f"{NS}/{prefix}", headers=auth_headers, json={"namespace_id": archived})
    assert restored.status_code == 200, restored.text
    await key_import.run_pass()

    grant = await NamespaceGrant.find_one({"namespace": prefix, "subject": app_key})
    assert grant is not None
    assert (grant.permission, grant.orphaned, grant.managed_by) == ("admin", False, "wip-deploy")
    assert (await Namespace.find_one({"prefix": prefix})).namespace_id == archived
    assert (await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)).namespace_id == archived
