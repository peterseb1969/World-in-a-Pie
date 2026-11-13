"""An install's reservation holds no namespace id while its namespace is absent.

A reservation the install's file makes (managed by wip-deploy) gives a name to
an app, whichever namespace holds it. It used to mint a namespace id like a
runtime reservation, and deleting the namespace minted a new one, so a platform
admin's ID-preserving restore of the app's namespace (from another install, or
after deleting it here) carried the archive's id onto a name "reserved here for
another namespace" and was refused with 409. The operator's only way out was to
remove the app, restore, and add it back. Now such a reservation holds the
namespace's id while the namespace exists and none otherwise; the namespace
created under it, restored or new, gives it its id. Runtime reservations keep
their ids and the 409.
"""

import json
import uuid

import pytest

from registry.models.grant import NamespaceGrant
from registry.models.namespace import Namespace
from registry.models.reservation import RuntimeReservation

NS = "/api/registry/namespaces"
KEYS = "/api/registry/api-keys"
BACKUP = "/api/registry/system-backup"
RESTORE = "/api/registry/system-restore"


def _p(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}"


def _uuid() -> str:
    return str(uuid.uuid4())


async def _reserved(prefix: str) -> RuntimeReservation | None:
    return await RuntimeReservation.find_one(RuntimeReservation.prefix == prefix)


@pytest.fixture
def install_reserves(monkeypatch, tmp_path):
    """Write the install's reservations file and import it."""
    from registry.services import key_import
    from registry.services.reservations import RESERVATIONS_FILE_ENV

    path = tmp_path / "namespace-reservations.json"
    monkeypatch.setenv(RESERVATIONS_FILE_ENV, str(path))

    async def reserve(*prefixes: str, app: str = "wip-kb") -> None:
        path.write_text(json.dumps({"reservations": [{"prefix": p, "app": app} for p in prefixes]}))
        await key_import.run_pass()

    return reserve


async def _delete(client, headers, prefix: str) -> None:
    await Namespace.get_motor_collection().update_one({"prefix": prefix}, {"$set": {"deletion_mode": "full"}})
    resp = await client.delete(f"{NS}/{prefix}", headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "completed", resp.text


@pytest.mark.asyncio
async def test_an_install_reservation_of_an_absent_namespace_holds_no_id(client, install_reserves):
    prefix = _p("kb")
    await install_reserves(prefix)
    r = await _reserved(prefix)
    assert r is not None and r.managed_by == "wip-deploy"
    assert r.namespace_id is None


@pytest.mark.asyncio
async def test_an_admin_restore_onto_an_install_reservation_keeps_the_archived_id(
    client, auth_headers, install_reserves,
):
    """The disaster-recovery case: the archive's namespace is re-created under
    its own id, the reservation adopts it, and the archive's orphans made for
    that id come alive."""
    prefix, archived = _p("kb"), _uuid()
    await install_reserves(prefix)
    await NamespaceGrant(
        namespace=prefix, subject="readers", subject_type="group", permission="read",
        granted_by="restore", orphaned=True, namespace_id=archived,
    ).insert()

    resp = await client.put(f"{NS}/{prefix}", headers=auth_headers, json={"namespace_id": archived})

    assert resp.status_code == 200, resp.text
    assert resp.json()["namespace_id"] == archived
    assert resp.json()["activated_orphan_grants"] == [f"{prefix}/group:readers"]
    assert (await _reserved(prefix)).namespace_id == archived


@pytest.mark.asyncio
async def test_delete_then_restore_with_the_app_installed(client, auth_headers, install_reserves):
    """The flow that needed remove-app / add-app: the namespace exists under
    the reservation, is deleted, and an archive from elsewhere is restored."""
    prefix, archived = _p("kb"), _uuid()
    await install_reserves(prefix)
    created = await client.put(f"{NS}/{prefix}", headers=auth_headers, json={})
    first = created.json()["namespace_id"]
    assert (await _reserved(prefix)).namespace_id == first

    await _delete(client, auth_headers, prefix)
    assert (await _reserved(prefix)).namespace_id is None

    restored = await client.put(f"{NS}/{prefix}", headers=auth_headers, json={"namespace_id": archived})
    assert restored.status_code == 200, restored.text
    assert restored.json()["namespace_id"] == archived
    assert (await _reserved(prefix)).namespace_id == archived
    # The reservation stays the install's, through every step.
    assert (await _reserved(prefix)).managed_by == "wip-deploy"


@pytest.mark.asyncio
async def test_a_plain_create_under_an_install_reservation_is_a_new_namespace(
    client, auth_headers, install_reserves,
):
    prefix = _p("kb")
    await install_reserves(prefix)
    created = await client.put(f"{NS}/{prefix}", headers=auth_headers, json={})
    assert created.status_code == 200, created.text
    ns_id = created.json()["namespace_id"]
    assert ns_id is not None
    assert (await _reserved(prefix)).namespace_id == ns_id
    # The next import keeps the namespace's id.
    await install_reserves(prefix)
    assert (await _reserved(prefix)).namespace_id == ns_id


@pytest.mark.asyncio
async def test_a_runtime_reservation_still_refuses_another_archived_id(client, auth_headers):
    prefix = _p("runtime")
    resp = await client.post(KEYS, headers=auth_headers, json={
        "name": _p("owner"), "namespaces": [prefix], "reserve": [{"prefix": prefix}],
    })
    assert resp.status_code == 201, resp.text
    refused = await client.put(f"{NS}/{prefix}", headers=auth_headers, json={"namespace_id": _uuid()})
    assert refused.status_code == 409, refused.text


@pytest.mark.asyncio
async def test_the_migration_leaves_an_install_reservation_without_an_id(client, install_reserves):
    from registry.services.namespace_identity import migrate_namespace_ids

    prefix = _p("kb")
    await install_reserves(prefix)
    await migrate_namespace_ids()
    assert (await _reserved(prefix)).namespace_id is None


@pytest.mark.asyncio
async def test_a_system_backup_leaves_it_out(client, auth_headers, install_reserves):
    """It holds no id and the target's deployer reserves its own names, so it
    is not archived (the review of 4a479b7f: archived, it came back as a
    runtime reservation with an id of its own, refusing the namespace restore
    after it, and its null was unreadable to an older Registry)."""
    prefix = _p("kb")
    await install_reserves(prefix)
    archive = (await client.post(BACKUP, headers=auth_headers)).json()
    assert prefix not in {r["prefix"] for r in archive["reservations"]}


@pytest.mark.asyncio
async def test_an_archive_may_omit_the_id_only_for_an_install_reservation(client, auth_headers):
    archive = (await client.post(BACKUP, headers=auth_headers)).json()
    archive["reservations"].append({"prefix": _p("odd"), "app": "someone", "namespace_id": None})
    resp = await client.post(RESTORE, json=archive, params={"dry_run": "false"}, headers=auth_headers)
    assert resp.status_code == 422, resp.text
    assert "namespace_id is required" in resp.text
